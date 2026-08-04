'use client'
// MA "Overview of Accounts" RECONCILIATION — the master-agent portal's STATED tiles next to the SAME
// tiles computed from our own ingested data, with a delta per tile, a per-merchant-account cross-check
// sorted by |delta|, and a drill-down into the rows behind any tile.
//
// Owner directive 2026-08-04: "create a similar report in the system and all activations and commission
// paid can be cross checked with this report to check the validity of the data in our system for
// activation count, commission, rebate etc."
//
// READ-ONLY WITH RESPECT TO PAY. Nothing on this page writes a commission, a plan, a payout or a
// schedule, and nothing here triggers a recalculation. It compares and reports.
//
// RULE TWO: every tile's definition (source table, aggregate, money columns, sign, row filter, and the
// uploaded report's header spellings) is CONFIG — edit it under ⚙ Tile mapping; the page never hard-codes
// a carrier. RULE THREE: period + account are pickers over what the org actually has. RULE FOUR: the
// export set (Excel/PDF/Print/Send) is on every table. RULE FIVE: the standard filter bar drives the
// tiles, the tables, the explainers AND the exports. There is no market/rep dimension here — this is
// processor account-keyed data with no store_mapping linkage (the same documented deviation as
// /commcalc/ma-commission).
import { useEffect, useRef, useState } from 'react'
import { api, apiUpload, fmt } from '@/lib/client'
import ReportExportBar, { type ExportColumn } from '@/components/ReportExportBar'
import { ReportShell } from '@/components/ReportShell'
import StandardFilterBar from '@/components/StandardFilterBar'
import { emptyStandardFilter, type StandardFilterValue } from '@/lib/standard-filters'
import { type EntityOption } from '@/lib/entity-picker-core'

const card: React.CSSProperties = { border: '1px solid var(--border)', borderRadius: 10, padding: '12px 14px', marginBottom: 14 }
const th: React.CSSProperties = { textAlign: 'left', padding: '6px 8px', fontSize: 11, color: 'var(--text2)' }
const td: React.CSSProperties = { padding: '5px 8px', fontSize: 13 }
const sel: React.CSSProperties = { padding: '5px 7px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 12, background: 'var(--surface)' }

type Tile = {
  tile_key: string; label: string; value_format: 'count' | 'money'
  uploaded: number | null; system: number | null; delta: number | null; delta_pct: number | null
  status: 'ok' | 'off' | 'unmapped' | 'no_report' | 'config_error'
  mapped: boolean; config_problems: string[]
  source: { table: string; agg: string; fields: string | null; sign: string; filter: string | null }
  uploaded_field: string; stated_from_total_row: boolean; note: string | null
}

const STATUS: Record<string, { tint: string; text: string; label: string }> = {
  ok: { tint: 'rgba(34,197,94,.10)', text: '#16a34a', label: 'matches' },
  off: { tint: 'rgba(239,68,68,.10)', text: '#dc2626', label: 'DELTA' },
  unmapped: { tint: 'rgba(148,163,184,.12)', text: 'var(--text2)', label: 'no source mapped' },
  no_report: { tint: 'rgba(148,163,184,.12)', text: 'var(--text2)', label: 'no report uploaded' },
  config_error: { tint: 'rgba(245,158,11,.12)', text: '#b45309', label: 'mapping error' },
}

const fmtVal = (v: number | null | undefined, kind: string) =>
  v === null || v === undefined ? '—' : (kind === 'money' ? fmt(v) : Math.round(v).toLocaleString())

function currentPeriod() {
  return new Date().toLocaleString('en-US', { month: 'long', year: 'numeric' })
}

export default function MaOverviewReconPage() {
  const [filt, setFilt] = useState<StandardFilterValue>(emptyStandardFilter(currentPeriod()))
  const [d, setD] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const [drill, setDrill] = useState<any>(null)
  const [showMap, setShowMap] = useState(false)
  const [mapping, setMapping] = useState<any>(null)
  const [showRates, setShowRates] = useState(false)
  const [rates, setRates] = useState<any>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  async function load(f: StandardFilterValue = filt) {
    setBusy(true); setMsg('')
    try {
      const qs = new URLSearchParams({ period: (f.period || currentPeriod()).trim() })
      if (f.stores.length) qs.set('accounts', f.stores.join(','))
      setD(await api(`/api/v1/commcalc/ma-overview-recon?${qs.toString()}`))
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    setBusy(false)
  }
  useEffect(() => { load() }, [])  // eslint-disable-line react-hooks/exhaustive-deps
  const didMount = useRef(false)
  useEffect(() => {
    if (!didMount.current) { didMount.current = true; return }
    load(filt)  // eslint-disable-line react-hooks/exhaustive-deps
  }, [filt])    // eslint-disable-line react-hooks/exhaustive-deps

  async function upload(f: File) {
    setBusy(true); setMsg('')
    try {
      const form = new FormData()
      form.append('file', f)
      const qs = new URLSearchParams({ period: (filt.period || currentPeriod()).trim() })
      const r: any = await apiUpload(`/api/v1/commcalc/ma-overview-recon/upload?${qs.toString()}`, form)
      setMsg(`✅ stored ${r?.saved ?? 0} row(s) for ${Object.keys(r?.periods || {}).join(', ') || 'the period'}`
        + ((r?.warnings || []).length ? ' — ' + r.warnings.join('; ') : ''))
      await load()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    setBusy(false)
    if (fileRef.current) fileRef.current.value = ''
  }

  async function openDrill(tileKey: string) {
    setDrill({ loading: true, tile: tileKey })
    try {
      const qs = new URLSearchParams({ tile: tileKey, period: (filt.period || currentPeriod()).trim() })
      if (filt.stores.length) qs.set('accounts', filt.stores.join(','))
      setDrill(await api(`/api/v1/commcalc/ma-overview-recon/drill?${qs.toString()}`))
    } catch (e: any) { setDrill({ error: e?.message || String(e), tile: tileKey }) }
  }

  async function openRates() {
    setShowRates(true)
    if (!rates) {
      try { setRates(await api(`/api/v1/commcalc/ma-overview-recon/rate-plan?period=${encodeURIComponent(filt.period || currentPeriod())}`)) }
      catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    }
  }

  async function saveRate(r: any) {
    setBusy(true); setMsg('')
    try {
      await api(`/api/v1/commcalc/ma-overview-recon/rate-plan/${r.month_index}`, {
        method: 'PUT',
        body: JSON.stringify({ rate_pct: Number(r.rate_pct) || 0, spiff_flat: Number(r.spiff_flat) || 0, effective_from: r.effective_from || null, note: r.note || null }),
      })
      setMsg(`✅ saved M${r.month_index}`)
      setRates(await api(`/api/v1/commcalc/ma-overview-recon/rate-plan?period=${encodeURIComponent(filt.period || currentPeriod())}`))
      await load()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    setBusy(false)
  }

  async function openMapping() {
    setShowMap(true)
    if (!mapping) {
      try { setMapping(await api('/api/v1/commcalc/ma-overview-recon/tiles')) }
      catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    }
  }

  async function saveTile(t: any) {
    setBusy(true); setMsg('')
    try {
      await api(`/api/v1/commcalc/ma-overview-recon/tiles/${encodeURIComponent(t.tile_key)}`, {
        method: 'PUT', body: JSON.stringify(t),
      })
      setMsg(`✅ saved “${t.label}”`)
      setMapping(await api('/api/v1/commcalc/ma-overview-recon/tiles'))
      await load()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    setBusy(false)
  }

  const tiles: Tile[] = d?.tiles || []
  const accountOpts: EntityOption[] = (d?.account_options || []).map((o: any) => ({ id: o.id, label: o.label }))
  const ex = d?.explain || {}
  const rep = d?.report || {}

  // RULE FOUR — the tile summary sheet + the per-account cross-check sheet.
  const col = (header: string, field: string, extra: Partial<ExportColumn> = {}): ExportColumn =>
    ({ header, field, get: (r: any) => r?.[field], ...extra })
  const tileCols: ExportColumn[] = [
    col('Tile', 'label'),
    col('Stated (report)', 'uploaded', { type: 'number' }),
    col('Ours (system)', 'system', { type: 'number' }),
    col('Delta', 'delta', { type: 'number' }),
    col('Delta %', 'delta_pct', { type: 'number' }),
    col('Status', 'status'),
    { header: 'Our source', field: 'src_text', get: (r: any) => srcText(r) },
  ]
  const acctCols: ExportColumn[] = [
    col('Account', 'account_id', { role: 'store' }),
    col('Name', 'account_name'),
    { header: 'In report', field: 'in_report', get: (r: any) => (r.in_report ? 'yes' : 'no') },
    { header: 'In system', field: 'in_system', get: (r: any) => (r.in_system ? 'yes' : 'no') },
    col('Rows', 'rows', { type: 'number' }),
    col('Distinct orders', 'distinct_orders', { type: 'number' }),
    col('Rows w/o IMEI', 'missing_imei_rows', { type: 'number' }),
    col('Unpaid lines', 'unpaid_lines', { type: 'number' }),
    col('M1 expected', 'exp_commissions_paid', { type: 'money', money: true }),
    ...tiles.filter(t => t.mapped).flatMap(t => {
      const kind = (t.value_format === 'money' ? 'money' : 'number') as ExportColumn['type']
      return [
        col(`${t.label} — stated`, `up_${t.tile_key}`, { type: kind, money: kind === 'money' }),
        col(`${t.label} — ours`, `sys_${t.tile_key}`, { type: kind, money: kind === 'money' }),
        col(`${t.label} — Δ`, `d_${t.tile_key}`, { type: kind, money: kind === 'money' }),
      ]
    }),
  ]

  return (
    <div style={{ padding: 20, maxWidth: 1500 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
        <h1 style={{ margin: 0 }}>MA Overview — cross-check</h1>
        <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 999, background: 'rgba(34,197,94,.12)', color: '#16a34a' }}>
          READ-ONLY · changes no pay
        </span>
        <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={openMapping}>⚙ Tile mapping</button>
      </div>
      <p style={{ color: 'var(--text2)', fontSize: 13, marginTop: 6, maxWidth: 980 }}>
        The master-agent portal's <b>Overview of Accounts</b> states a fixed tile set for a period. This page
        puts the report's <b>stated</b> numbers next to the <b>same tiles computed from our ingested data</b>
        {' '}(MA Commission Details + MA Daily Tx) and shows the delta — so activation counts, commission and
        rebates can be validated against the source. Upload the report for the period, then read the deltas.
        Nothing here adjusts a payout.
      </p>

      <StandardFilterBar
        value={filt}
        onChange={setFilt}
        periodMode="month"
        periods={d?.periods || []}
        show={{ period: true, stores: true, markets: false, reps: false }}
        storeOptions={accountOpts}
        storeLabel="Accounts…"
        right={
          <>
            <input ref={fileRef} type="file" accept=".csv,.txt,.xls,.xlsx" style={{ display: 'none' }}
              onChange={e => { const f = e.target.files?.[0]; if (f) upload(f) }} />
            <button className="btn" style={{ fontSize: 12 }} disabled={busy}
              onClick={() => fileRef.current?.click()}>⬆ Upload overview report</button>
            <button className="btn btn-secondary" style={{ fontSize: 12 }} disabled={busy}
              onClick={() => load()}>↻ Refresh</button>
            <ReportExportBar
              title={`MA Overview cross-check — ${d?.period || ''}`}
              filename={`ma_overview_recon_${(d?.period || '').replace(/\W+/g, '_').toLowerCase()}`}
              sheets={[
                { name: 'Tiles', columns: tileCols, rows: tiles },
                { name: 'By account', columns: acctCols, rows: d?.per_account || [] },
              ]}
            />
          </>
        }
      />

      {msg && <div style={{ ...card, background: 'var(--surface2)' }}>{msg}</div>}
      {busy && <div style={{ color: 'var(--text2)', fontSize: 13, marginBottom: 8 }}>Loading…</div>}

      {/* ── the stored report's provenance ── */}
      <div style={{ ...card, display: 'flex', gap: 18, flexWrap: 'wrap', alignItems: 'center', fontSize: 13 }}>
        {rep.present ? (
          <>
            <span>📄 <b>Report on file</b> for {d?.period}: {rep.rows} row(s), {rep.accounts} account(s)
              {rep.has_total_row ? ' + a report-level total row' : ''}</span>
            {rep.source_file && <span style={{ color: 'var(--text2)' }}>from <code>{rep.source_file}</code></span>}
            {rep.uploaded_at && <span style={{ color: 'var(--text2)' }}>stored {String(rep.uploaded_at).slice(0, 19).replace('T', ' ')}</span>}
            {rep.stated_abbreviated && <span style={{ color: '#b45309' }}>⚠ stated values were abbreviated (1.1K / $28.3K) and expanded on ingest — small deltas are rounding, not data</span>}
          </>
        ) : (
          <span style={{ color: 'var(--text2)' }}>
            No overview report stored for <b>{d?.period}</b> yet — upload it above (CSV or Excel; either the
            per-account export or a two-column tile list works). Until then only <b>our</b> side is shown.
          </span>
        )}
      </div>

      {/* ── the tiles ── */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, marginBottom: 16 }}>
        {tiles.map(t => {
          const st = STATUS[t.status] || STATUS.ok
          return (
            <div key={t.tile_key} title={t.note || ''}
              onClick={() => t.mapped && openDrill(t.tile_key)}
              style={{
                flex: '1 1 260px', minWidth: 250, border: '1px solid var(--border)', borderRadius: 10,
                padding: '11px 13px', background: st.tint, cursor: t.mapped ? 'pointer' : 'default',
              }}>
              <div style={{ fontSize: 12, color: 'var(--text2)', display: 'flex', justifyContent: 'space-between' }}>
                <span>{t.label}</span>
                <span style={{ color: st.text, fontWeight: 600 }}>{st.label}</span>
              </div>
              <div style={{ display: 'flex', gap: 14, marginTop: 6, alignItems: 'baseline' }}>
                <div>
                  <div style={{ fontSize: 10, color: 'var(--text2)' }}>STATED</div>
                  <div style={{ fontSize: 18, fontWeight: 600 }}>{fmtVal(t.uploaded, t.value_format)}</div>
                </div>
                <div>
                  <div style={{ fontSize: 10, color: 'var(--text2)' }}>OURS</div>
                  <div style={{ fontSize: 18, fontWeight: 600 }}>
                    {t.mapped ? fmtVal(t.system, t.value_format) : <span style={{ fontSize: 12, color: 'var(--text2)' }}>not mapped</span>}
                  </div>
                </div>
                <div>
                  <div style={{ fontSize: 10, color: 'var(--text2)' }}>Δ</div>
                  <div style={{ fontSize: 18, fontWeight: 600, color: t.delta ? st.text : undefined }}>
                    {t.delta === null || t.delta === undefined ? '—' : (t.delta > 0 ? '+' : '') + fmtVal(t.delta, t.value_format)}
                  </div>
                </div>
              </div>
              <div style={{ fontSize: 11, color: 'var(--text2)', marginTop: 6 }}>{srcText(t)}</div>
              {!!t.config_problems?.length && (
                <div style={{ fontSize: 11, color: '#b45309', marginTop: 4 }}>⚠ {t.config_problems.join('; ')}</div>
              )}
              {t.stated_from_total_row && (
                <div style={{ fontSize: 11, color: 'var(--text2)', marginTop: 4 }}>stated from the report-level total row</div>
              )}
            </div>
          )
        })}
      </div>

      {/* ── assumptions, stated on the page ── */}
      {!!(d?.assumptions || []).length && (
        <div style={{ ...card, background: 'var(--surface2)' }}>
          <div style={{ fontWeight: 600, marginBottom: 6, fontSize: 13 }}>What this page is assuming</div>
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12, color: 'var(--text2)' }}>
            {(d.assumptions || []).map((a: any, i: number) => (
              <li key={i} style={{ marginBottom: 3 }}><b>{a.tile}</b> — {a.text}</li>
            ))}
          </ul>
        </div>
      )}

      {/* ── per-account cross-check ── */}
      <ReportShell
        title="Per-account cross-check"
        subtitle={`${d?.period || ''} · sorted by biggest relative delta first · stated (report) vs ours (system)`}
        filename={`ma_overview_by_account_${(d?.period || '').replace(/\W+/g, '_').toLowerCase()}`}
        columns={acctCols}
        rows={d?.per_account || []}
        stickyHeader
        totals
        compact
        rowStyle={(r: any) => (!r.in_report || !r.in_system ? { background: 'rgba(245,158,11,.10)' } : undefined)}
      />

      {/* ── the owner's cross-check: what the carrier's PLAN says the M1 commission should be ── */}
      {d?.expected_commission && (
        <div style={{ ...card }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
            <div style={{ fontWeight: 600 }}>Commission check — what the plan says it should be</div>
            <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={openRates}>⚙ Carrier rate plan</button>
          </div>
          <div style={{ fontSize: 12, color: 'var(--text2)', margin: '4px 0 10px' }}>
            {d.expected_commission.basis}
          </div>
          <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap' }}>
            <Explain title={`EXPECTED (M1 @ ${d.expected_commission.rate_pct}% of MRC)`}
              money value={d.expected_commission.expected} detail={d.expected_commission.formula} />
            <Explain title="OURS (what we hold)" money value={d.expected_commission.system}
              detail={`${d.expected_commission.qualifying_activations?.toLocaleString()} qualifying activations`} />
            <Explain title="STATED (carrier report)" money value={d.expected_commission.stated}
              detail={d.expected_commission.stated == null ? 'no report uploaded' : ''} />
            <Explain title="Expected − ours" money bad value={d.expected_commission.expected_vs_system}
              detail="If this is large, either the plan rate is out of date or lines were underpaid." />
            <Explain title="Expected − stated" money bad value={d.expected_commission.expected_vs_stated}
              detail="What the plan says, versus what the carrier says it paid." />
            <Explain title="MRC base" money value={d.expected_commission.mrc_total}
              detail={`avg ${fmt(d.expected_commission.avg_mrc || 0)} per activation`} />
          </div>
          {d.rate_plan && (
            <div style={{ fontSize: 11, color: 'var(--text2)', marginTop: 8 }}>
              rate plan: <b>{d.rate_plan.source === 'org_config' ? 'this tenant’s saved plan' : 'built-in default'}</b>
              {' — '}{(d.rate_plan.rates || []).map((r: any) => `M${r.month_index} ${r.rate_pct}%`).join(' · ')}
              {'. '}{d.rate_plan.note}
            </div>
          )}
        </div>
      )}

      {/* ── the activation vocabulary the owner's rule turns on ── */}
      {d?.activation_vocabulary && (
        <div style={card}>
          <div style={{ fontWeight: 600, marginBottom: 4 }}>Value vocabulary — what counts as an activation</div>
          <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 8 }}>
            {d.activation_vocabulary.note}
          </div>
          <div style={{ fontSize: 12, marginBottom: 6 }}>
            Current rule: <code>{d.activation_vocabulary.definition || '(none)'}</code>
          </div>
          <table style={{ borderCollapse: 'collapse' }}>
            <thead><tr>{['Activation Type', 'Rows', 'Counted?'].map(h => <th key={h} style={th}>{h}</th>)}</tr></thead>
            <tbody>
              {(d.activation_vocabulary.values || []).map((v: any) => (
                <tr key={v.value} style={{ borderTop: '1px solid var(--border)' }}>
                  <td style={td}>{v.value}</td>
                  <td style={{ ...td, textAlign: 'right' }}>{v.rows.toLocaleString()}</td>
                  <td style={{ ...td, color: v.counted ? '#16a34a' : 'var(--text2)' }}>
                    {v.counted ? 'counted' : 'excluded'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* ── delta explainers ── */}
      {d && (
        <div style={{ ...card, marginTop: 16 }}>
          <div style={{ fontWeight: 600, marginBottom: 8 }}>What could explain a delta</div>
          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', fontSize: 13 }}>
            <Explain title="Accounts only in the report"
              value={(ex.accounts_only_in_report || []).length}
              detail={(ex.accounts_only_in_report || []).map((a: any) => a.account_id + (a.account_name ? ` (${a.account_name})` : '')).join(', ')}
              note="The portal states these accounts; we have no MA rows for them this period — a missing pull or a mailbox rule." />
            <Explain title="Accounts only in our data"
              value={(ex.accounts_only_in_system || []).length}
              detail={(ex.accounts_only_in_system || []).map((a: any) => `${a.account_id} (${a.rows} rows)`).join(', ')}
              note="We have rows for these accounts; the report does not mention them — check the report is the full export." />
            <Explain title="Rows with no IMEI"
              value={ex.missing_imei?.rows} detail={`of ${ex.missing_imei?.of_rows ?? 0} rows`}
              note={ex.missing_imei?.note} />
            <Explain title="Extra lines vs activation orders"
              value={ex.multi_line_activations?.extra_lines}
              detail={`${ex.multi_line_activations?.rows ?? 0} rows / ${ex.multi_line_activations?.distinct_activation_orders ?? 0} distinct orders`}
              note={ex.multi_line_activations?.note} />
            <Explain title="Rows on the month's first/last day"
              value={(ex.date_boundary?.rows_on_first_day || 0) + (ex.date_boundary?.rows_on_last_day || 0)}
              detail={`${ex.date_boundary?.rows_on_first_day ?? 0} on ${ex.date_boundary?.period_first_day ?? '?'} · ${ex.date_boundary?.rows_on_last_day ?? 0} on ${ex.date_boundary?.period_last_day ?? '?'}`}
              note={ex.date_boundary?.note} />
            <Explain title="Rows dated OUTSIDE the period"
              value={ex.date_boundary?.rows_dated_outside_period} bad
              detail={(ex.date_boundary?.outside_dates || []).map((x: any) => `${x.tx_date}×${x.rows}`).join(', ')}
              note="A row filed under this period whose own transaction date is in another month — a real data defect (the month-boundary / period-spelling class), not a rounding effect." />
            <Explain title="Residual rows dated outside the period"
              value={ex.residual_date_boundary?.rows_dated_outside_period} bad
              detail={(ex.residual_date_boundary?.outside_dates || []).map((x: any) => `${x.tx_date}×${x.rows}`).join(', ')}
              note="Same check on the MA Daily Tx feed, which the Residual tile reads." />
          </div>
          {ex.basis_alternatives && (
            <div style={{ marginTop: 12 }}>
              <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 4 }}>Basis alternatives (money tiles)</div>
              <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 6 }}>{ex.basis_alternatives.note}</div>
              <table style={{ borderCollapse: 'collapse' }}>
                <tbody>
                  {Object.entries(ex.basis_alternatives).filter(([k]) => k !== 'note').map(([k, v]: any) => (
                    <tr key={k}><td style={td}>{k.replace(/_/g, ' ')}</td><td style={{ ...td, textAlign: 'right' }}>{fmt(Number(v) || 0)}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* ── candidates for the deliberately-unmapped tiles ── */}
      {d?.unmapped_candidates && tiles.some(t => !t.mapped) && (
        <div style={card}>
          <div style={{ fontWeight: 600, marginBottom: 4 }}>Unmapped tile candidates</div>
          <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 8 }}>
            “Commissions Not Eligible” and “Appeal Count” have <b>no system source mapped on purpose</b> — the
            source report's definition is not known, and a fake 0 would be worse than an honest blank. These
            are the real value distributions in our data for the columns that could define them; pick the
            right values and save them under ⚙ Tile mapping.
          </div>
          <div style={{ display: 'flex', gap: 22, flexWrap: 'wrap' }}>
            {Object.entries(d.unmapped_candidates).map(([field, vals]: any) => (
              <div key={field}>
                <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 3 }}>{field}</div>
                <table style={{ borderCollapse: 'collapse' }}>
                  <tbody>
                    {(vals || []).slice(0, 12).map((v: any) => (
                      <tr key={v.value}>
                        <td style={td}>{v.value}</td>
                        <td style={{ ...td, textAlign: 'right', color: 'var(--text2)' }}>{v.rows.toLocaleString()}</td>
                      </tr>
                    ))}
                    {!(vals || []).length && <tr><td style={td}>—</td></tr>}
                  </tbody>
                </table>
              </div>
            ))}
          </div>
        </div>
      )}

      {d && (
        <div style={{ fontSize: 11, color: 'var(--text2)', marginTop: 10 }}>
          tile mapping: <b>{d.config_source === 'org_config' ? 'this tenant’s saved rows' : 'code defaults'}</b>
          {' · '}aggregates: {Object.entries(d.cube_source || {}).map(([k, v]) => `${k}=${v}`).join(', ') || '—'}
          {Object.values(d.cube_source || {}).includes('python_fallback') &&
            ' — migration 268 has not been run yet, so the totals came from a paged scan (slower, same numbers).'}
        </div>
      )}

      {/* ── drill-down ── */}
      {drill && (
        <div style={{ ...card, marginTop: 16 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div style={{ fontWeight: 600 }}>
              Rows behind “{drill.label || drill.tile}”{drill.filter ? ` — ${drill.filter}` : ''}
            </div>
            <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={() => setDrill(null)}>Close</button>
          </div>
          {drill.loading && <div style={{ fontSize: 13, color: 'var(--text2)' }}>Loading…</div>}
          {drill.error && <div style={{ fontSize: 13, color: '#dc2626' }}>❌ {drill.error}</div>}
          {drill.unmapped && <div style={{ fontSize: 13, color: 'var(--text2)' }}>{drill.note}</div>}
          {!!(drill.rows || []).length && (
            <>
              <div style={{ fontSize: 12, color: 'var(--text2)', margin: '6px 0' }}>
                {drill.worklist
                  ? `${drill.matched?.toLocaleString()} activation line(s) paid NOTHING — the follow-up worklist, oldest first. Chasing these is manual; this page records nothing.`
                  : `${drill.matched?.toLocaleString()} matching row(s) in ${drill.source_table}`}
                {drill.capped ? ` — showing the first ${drill.returned}` : ''}
              </div>
              <ReportShell
                title={`${drill.label} — underlying rows`}
                filename={`ma_overview_drill_${drill.tile}`}
                columns={Object.keys(drill.rows[0]).map((k): ExportColumn => ({
                  header: k.replace(/_/g, ' '), field: k, get: (r: any) => r?.[k],
                  type: (typeof drill.rows[0][k] === 'number' ? 'number' : 'text'),
                }))}
                rows={drill.rows}
                compact stickyHeader
              />
            </>
          )}
        </div>
      )}

      {/* ── carrier rate plan editor (RULE TWO — rates change, spiffs are temporary) ── */}
      {showRates && (
        <div style={{ ...card, marginTop: 16 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
            <div style={{ fontWeight: 600 }}>⚙ Carrier rate plan — M1–M6</div>
            <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={() => setShowRates(false)}>Close</button>
          </div>
          <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 10 }}>
            What the carrier pays per month leg, as a percentage of the line's MRC plus any flat spiff.
            The owner's standing Total plan is <b>M1 50%, M2–M6 75%</b>; M3–M6 are temporary spiffs, so
            expect to change them here rather than in code. Leave "in force from" blank for "always".
            <b> This changes the EXPECTED column only — it pays nobody.</b>
          </div>
          <table style={{ borderCollapse: 'collapse' }}>
            <thead><tr>{['Month', '% of MRC', 'Flat spiff $', 'In force from', 'Note', ''].map(h => <th key={h} style={th}>{h}</th>)}</tr></thead>
            <tbody>
              {(rates?.rates || []).map((r: any, i: number) => (
                <tr key={r.month_index} style={{ borderTop: '1px solid var(--border)' }}>
                  <td style={td}>M{r.month_index}</td>
                  <td style={td}><input style={{ ...sel, width: 80 }} value={r.rate_pct ?? ''} onChange={e => patchRate(i, { rate_pct: e.target.value })} /></td>
                  <td style={td}><input style={{ ...sel, width: 80 }} value={r.spiff_flat ?? ''} onChange={e => patchRate(i, { spiff_flat: e.target.value })} /></td>
                  <td style={td}><input type="date" style={sel} value={String(r.effective_from || '').slice(0, 10)} onChange={e => patchRate(i, { effective_from: e.target.value })} /></td>
                  <td style={td}><input style={{ ...sel, width: 300 }} value={r.note || ''} onChange={e => patchRate(i, { note: e.target.value })} /></td>
                  <td style={td}><button className="btn" style={{ fontSize: 11 }} disabled={busy} onClick={() => saveRate(r)}>Save</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* ── tile mapping editor (RULE TWO) ── */}
      {showMap && (
        <div style={{ ...card, marginTop: 16 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
            <div style={{ fontWeight: 600 }}>⚙ Tile mapping — what each tile means</div>
            <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={() => setShowMap(false)}>Close</button>
          </div>
          <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 10 }}>
            Each tile names the report column that <b>states</b> it and the aggregate that <b>computes</b> it
            from our data. Editing this changes what the cross-check compares — it changes no payout.
            {mapping?.source === 'code_default' && ' These are the built-in defaults; saving any tile creates this tenant’s own rows.'}
          </div>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ borderCollapse: 'collapse', width: '100%' }}>
              <thead><tr>
                {['Tile', 'Source table', 'Agg', 'Money columns', 'Sign', 'Filter', 'Report column', 'Header aliases', ''].map(h =>
                  <th key={h} style={th}>{h}</th>)}
              </tr></thead>
              <tbody>
                {(mapping?.tiles || []).map((t: any, i: number) => (
                  <tr key={t.tile_key} style={{ borderTop: '1px solid var(--border)' }}>
                    <td style={td}>{t.label}</td>
                    <td style={td}>
                      <select style={sel} value={t.source_table || ''} onChange={e => patchTile(i, { source_table: e.target.value })}>
                        <option value="">(none)</option>
                        {Object.keys(mapping?.sources || {}).map(s => <option key={s} value={s}>{s}</option>)}
                      </select>
                    </td>
                    <td style={td}>
                      <select style={sel} value={t.agg || 'none'} onChange={e => patchTile(i, { agg: e.target.value })}>
                        {(mapping?.aggs || []).map((a: string) => <option key={a} value={a}>{a}</option>)}
                      </select>
                    </td>
                    <td style={td}>
                      <select style={sel} multiple size={3}
                        value={String(t.value_fields || '').split(',').filter(Boolean)}
                        onChange={e => patchTile(i, { value_fields: Array.from(e.target.selectedOptions).map(o => o.value).join(',') })}>
                        {((mapping?.sources || {})[t.source_table]?.money || []).map((m: string) => <option key={m} value={m}>{m}</option>)}
                      </select>
                    </td>
                    <td style={td}>
                      <select style={sel} value={t.sign || 'as_is'} onChange={e => patchTile(i, { sign: e.target.value })}>
                        {(mapping?.signs || []).map((s: string) => <option key={s} value={s}>{s}</option>)}
                      </select>
                    </td>
                    <td style={td}>
                      <select style={sel} value={t.filter_field || ''} onChange={e => patchTile(i, { filter_field: e.target.value })}>
                        <option value="">(no filter)</option>
                        {((mapping?.sources || {})[t.source_table]?.dims || []).map((m: string) => <option key={m} value={m}>{m}</option>)}
                      </select>
                      <select style={{ ...sel, marginLeft: 4 }} value={t.filter_op || 'eq'} onChange={e => patchTile(i, { filter_op: e.target.value })}>
                        {(mapping?.filter_ops || []).map((o: string) => <option key={o} value={o}>{o}</option>)}
                      </select>
                      <input style={{ ...sel, marginLeft: 4, width: 130 }} value={t.filter_value || ''}
                        placeholder="value(s)" onChange={e => patchTile(i, { filter_value: e.target.value })} />
                    </td>
                    <td style={td}>
                      <select style={sel} value={t.uploaded_field || ''} onChange={e => patchTile(i, { uploaded_field: e.target.value })}>
                        <option value="">(none)</option>
                        {(mapping?.upload_metrics || []).map((m: string) => <option key={m} value={m}>{m}</option>)}
                      </select>
                    </td>
                    <td style={td}>
                      <input style={{ ...sel, width: 230 }} value={t.uploaded_aliases || ''}
                        onChange={e => patchTile(i, { uploaded_aliases: e.target.value })} />
                    </td>
                    <td style={td}>
                      <button className="btn" style={{ fontSize: 11 }} disabled={busy} onClick={() => saveTile(t)}>Save</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div style={{ fontSize: 11, color: 'var(--text2)', marginTop: 8 }}>
            {mapping?.note}
          </div>
        </div>
      )}
    </div>
  )

  function patchRate(i: number, patch: any) {
    setRates((m: any) => {
      const rs = [...(m?.rates || [])]
      rs[i] = { ...rs[i], ...patch }
      return { ...m, rates: rs }
    })
  }

  function patchTile(i: number, patch: any) {
    setMapping((m: any) => {
      const tiles = [...(m?.tiles || [])]
      tiles[i] = { ...tiles[i], ...patch }
      return { ...m, tiles }
    })
  }
}

function srcText(t: any): string {
  if (!t?.mapped) return t?.note ? 'no system source mapped' : 'no system source mapped'
  const s = t.source || {}
  const base = s.agg === 'count' ? `count of ${s.table}` : `Σ ${s.fields} on ${s.table}`
  return base + (s.filter ? ` where ${s.filter}` : '') + (s.agg === 'sum' && s.sign !== 'as_is' ? ` (${s.sign})` : '')
}

function Explain({ title, value, detail, note, bad, money }: {
  title: string; value: any; detail?: string; note?: string; bad?: boolean; money?: boolean
}) {
  const n = Number(value || 0)
  const blank = value === null || value === undefined
  return (
    <div style={{ flex: '1 1 240px', minWidth: 230, border: '1px solid var(--border)', borderRadius: 8, padding: '9px 11px' }} title={note || ''}>
      <div style={{ fontSize: 12, color: 'var(--text2)' }}>{title}</div>
      <div style={{ fontSize: 18, fontWeight: 600, color: bad && Math.abs(n) > 0.005 ? '#dc2626' : undefined }}>
        {blank ? '—' : money ? fmt(n) : n.toLocaleString()}
      </div>
      {detail && <div style={{ fontSize: 11, color: 'var(--text2)', marginTop: 3, wordBreak: 'break-word' }}>{detail}</div>}
    </div>
  )
}
