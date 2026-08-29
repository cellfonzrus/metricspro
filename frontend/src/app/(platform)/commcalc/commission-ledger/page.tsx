'use client'
import { useEffect, useMemo, useState } from 'react'
import { api, apiUpload } from '@/lib/client'
import ReportExportBar, { type ExportColumn } from '@/components/ReportExportBar'
import EntityPicker from '@/components/EntityPicker'
import { optionsFromRows } from '@/lib/standard-filters'

// Canonical Commission Ledger (SAP-style) — normalise ANY carrier's commission/tx file into FIVE canonical
// buckets: Commission / Spiff / Equipment rebate / Residual-monthly / Auto Pay residual. A payout paid over
// many months stays one category but keeps its payment month (shown in the matrix). Negative amounts are
// payouts; positives are bill/activation payments (counted separately, never in the buckets). Templates
// (Total / Boost / your own) namespace the classification rules. Backed by commcalc.commission_ledger +
// commission_category_map (migration 071); the classifier degrades to built-in defaults until 071 is run.

type Summ = {
  source_report: string; period: string; line_count: number; payout_total: number; charge_total: number
  other_total: number; other_count: number
  categories: Record<string, { total: number; count: number }>
  category_labels: Record<string, string>; by_month: Record<string, number>
  // COMMISSION LEG (owner 2026-08-04) — the same payout money, additionally split into the leg of the
  // activation's life it belongs to. `by_category_leg[category]` sums to that category's own total, and
  // `legs` sums to payout_total; `leg_identity_ok` is the backend's own proof of both.
  legs?: Record<string, number>
  leg_labels?: Record<string, string>
  leg_buckets?: string[]
  by_category_leg?: Record<string, Record<string, number>>
  leg_ladder?: Record<string, number>
  leg_unmapped?: { label: string; amount: number; lines: number }[]
  leg_unmapped_total?: number
  leg_identity_ok?: boolean
  leg_basis?: string
}
const LEG_ORDER = ['m1', 'trailing', 'unsplit']
const LEG_FALLBACK: Record<string, string> = { m1: '1st Month', trailing: 'M2–M12', unsplit: 'Unsplit' }
type Tmpl = { key: string; label: string; builtin: boolean; rule_count: number; ma_syncable?: boolean; ma_sources?: string[] }
// Provenance: which ingest populated a period, and when. `raw_available` counts the rows sitting in the
// raw MA tables for that period — so a period whose feed has moved on while the ledger hasn't is visible.
type OriginRow = { origin: string; label: string; lines: number; payout_total: number; last_at: string | null }
type ProvPeriod = { period: string; lines: number; payout_total: number; origins: OriginRow[]
  raw_available: Record<string, number>; raw_rows: number; overlap: boolean; synced: boolean; stale: boolean }
type Prov = { ready: boolean; migration: string | null; periods: ProvPeriod[]
  raw_sources: { report_key: string; source_table: string; missing: boolean; periods: number; rows: number; truncated?: boolean }[] }
type SyncField = { target_field: string; header: string; col: string; confidence: string; label: string }
type SyncSource = {
  report_key: string; source_table: string; kind: string; label: string; ceiling: number
  read: { rows: number; matched_by: string | null; table_missing: boolean; truncated: boolean; error: string | null }
  diag: { rows_in: number; lines_out: number; excluded_ceiling: number; excluded_ceiling_total: number
    skipped_empty_amount: number; skipped_no_content: number; refused: string | null; amount_col?: string
    amount_header?: string; amount_confidence?: string
    by_component?: Record<string, { label: string; lines: number; total: number; refused?: string }> }
  mapped_fields: SyncField[]; unresolved_fields: { target_field: string; header: string; label: string }[]
  synthesized_fields: string[]; components: { col: string; label: string; payment_month: number | null }[]
  column_map_source: string
}
type SyncPrev = {
  ready: boolean; migration: string | null; would_write: number; saved?: number
  sources: SyncSource[]; summary: Summ; observed: any[]; unmapped: any[]
  guard: { rows_in: number; lines_out: number; excluded_ceiling: number; excluded_ceiling_total: number
    skipped_empty_amount: number; skipped_no_content: number
    excluded_examples: { source_table: string; column: string; label: string | null; amount: number }[]
    refused: { report_key: string; source_table: string; reason: string }[] }
  existing_by_origin: OriginRow[]; overlap_note: string | null; warnings: string[]
}
type RepRow = { rep: string; lines: number; ledger_payout: number; live_payout: number | null; matched: boolean } & Record<string, number>
type ByRep = { reps: RepRow[]; totals: Record<string, number>; matched_count: number; rep_count: number; category_labels: Record<string, string> }
const CATS = ['commission', 'spiff', 'equipment_rebate', 'residual_monthly', 'autopay_residual']
const money = (n: number) => (n || 0).toLocaleString('en-US', { style: 'currency', currency: 'USD' })
const inp: React.CSSProperties = { padding: '6px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const tile: React.CSSProperties = { border: '1px solid var(--border)', borderRadius: 10, padding: 14, minWidth: 150, background: 'var(--surface)' }

export default function CommissionLedgerPage() {
  const [tmpls, setTmpls] = useState<Tmpl[]>([])
  // Default to the Boost template (the house/1st-tenant carrier). The page opened on the Total
  // ('ma_daily_tx') template, which made the Boost side show Total's numbers — Total belongs to
  // its own tenant. Switch templates from the picker for other carriers.
  const [src, setSrc] = useState('boost')
  const [period, setPeriod] = useState('')
  const [summ, setSumm] = useState<Summ | null>(null)
  const [labels, setLabels] = useState<Record<string, string>>({})
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const [drill, setDrill] = useState<{ cat: string; rows: any[] } | null>(null)
  const [view, setView] = useState<'cat' | 'rep'>('cat')
  const [byRep, setByRep] = useState<ByRep | null>(null)
  const [selReps, setSelReps] = useState<string[]>([])   // RULE FIVE rep(s) multi — By-rep view
  // ── MA-data refresh (owner directive 2026-07-30) + provenance honesty ──────────────────────────
  const [syncReady, setSyncReady] = useState(true)      // migration 251 applied?
  const [syncMig, setSyncMig] = useState('')
  const [prev, setPrev] = useState<SyncPrev | null>(null)   // the preview of a refresh (writes nothing)
  const [prov, setProv] = useState<Prov | null>(null)
  const [origin, setOrigin] = useState('')             // '' = every source (unchanged behaviour)
  const [applying, setApplying] = useState(false)
  const tmpl = tmpls.find(t => t.key === src)

  const oq = origin ? `&origin=${encodeURIComponent(origin)}` : ''
  async function loadTemplates() {
    try {
      const d = await api('/api/v1/commcalc/commission-ledger/templates')
      setTmpls(d?.templates || [])
      setLabels(d?.category_labels || {})
      setSyncReady(d?.sync_ready !== false); setSyncMig(d?.sync_migration || '')
    } catch (e: any) { setMsg(e?.message || 'Load failed') }
  }
  async function loadSummary() {
    try {
      const qs = `?source_report=${encodeURIComponent(src)}${period ? '&period=' + encodeURIComponent(period) : ''}${oq}`
      const d = await api('/api/v1/commcalc/commission-ledger/summary' + qs)
      setSumm(d); setLabels(d?.category_labels || labels)
    } catch (e: any) { setMsg(e?.message || 'Load failed') }
  }
  async function loadByRep() {
    try {
      const qs = `?source_report=${encodeURIComponent(src)}${period ? '&period=' + encodeURIComponent(period) : ''}${oq}`
      setByRep(await api('/api/v1/commcalc/commission-ledger/by-rep' + qs))
    } catch (e: any) { setMsg(e?.message || 'Load failed') }
  }
  async function loadProvenance() {
    try {
      setProv(await api(`/api/v1/commcalc/commission-ledger/provenance?source_report=${encodeURIComponent(src)}`))
    } catch { setProv(null) }
  }
  useEffect(() => { loadTemplates() }, [])            // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { loadSummary(); setDrill(null); setSelReps([]) }, [src, period, origin])  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { setPrev(null); loadProvenance() }, [src])  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (view === 'rep') loadByRep() }, [view, src, period, origin])  // eslint-disable-line react-hooks/exhaustive-deps

  function flash(m: string) { setMsg(m); setTimeout(() => setMsg(''), 4000) }

  async function upload(file: File) {
    setBusy(true)
    try {
      const fd = new FormData()
      fd.append('file', file); fd.append('source_report', src); fd.append('period', period)
      const r = await apiUpload('/api/v1/commcalc/commission-ledger/import', fd)
      flash(`Imported ${r?.saved} lines — payouts ${money(r?.summary?.payout_total)}${r?.summary?.other_count ? `, ${r.summary.other_count} unmapped` : ''}`)
      loadSummary()
    } catch (e: any) { flash(e?.message || 'Import failed — is migration 071 applied?') }
    setBusy(false)
  }
  async function openDrill(cat: string) {
    try {
      const qs = `?source_report=${encodeURIComponent(src)}${period ? '&period=' + encodeURIComponent(period) : ''}&category=${cat}&limit=500${oq}`
      const d = await api('/api/v1/commcalc/commission-ledger/rows' + qs)
      setDrill({ cat, rows: d?.rows || [] })
    } catch (e: any) { flash(e?.message || 'Drill failed') }
  }

  // ── refresh from the raw MA tables: PREVIEW first (writes nothing), then apply ─────────────────
  async function previewSync() {
    if (!period) { flash('Enter the period first (e.g. June 2026)'); return }
    setBusy(true)
    try {
      setPrev(await api(`/api/v1/commcalc/commission-ledger/ma-sync/preview?source_report=${encodeURIComponent(src)}&period=${encodeURIComponent(period)}`))
    } catch (e: any) { flash(e?.message || 'Preview failed'); setPrev(null) }
    setBusy(false)
  }
  async function applySync() {
    if (!period) return
    setApplying(true)
    try {
      const r: SyncPrev = await api(`/api/v1/commcalc/commission-ledger/ma-sync?source_report=${encodeURIComponent(src)}&period=${encodeURIComponent(period)}`, { method: 'POST' })
      setPrev(r)
      flash(`Refreshed from MA data — ${r?.saved ?? 0} line(s), payouts ${money(r?.summary?.payout_total)}${r?.summary?.other_count ? `, ${r.summary.other_count} unmapped` : ''}`)
      loadSummary(); loadProvenance()
    } catch (e: any) { flash(e?.message || 'Refresh failed') }
    setApplying(false)
  }
  const provPeriod = prov?.periods?.find(p => p.period === period)
  const provOrigins = provPeriod?.origins || []

  // month columns present across the buckets
  const months = Array.from(new Set(Object.keys(summ?.by_month || {}).map(k => Number(k.split('|')[1])).filter(m => m > 0))).sort((a, b) => a - b)
  const cell = (cat: string, m: number) => summ?.by_month?.[`${cat}|${m}`] || 0

  // RULE FOUR export — reflects the ACTIVE view (By category month-matrix vs By rep). Money-flagged.
  const catExportCols: ExportColumn[] = [
    { header: 'Category', get: (r: any) => r.cat },
    ...months.map(m => ({ header: `M${m}`, money: true, get: (r: any) => r.months[m] || 0 })),
    { header: 'Total', money: true, get: (r: any) => r.total },
  ]
  const catExportRows = CATS.map(c => ({
    cat: labels[c] || c, total: summ?.categories[c]?.total || 0,
    months: Object.fromEntries(months.map(m => [m, cell(c, m)])),
  }))
  const repExportCols: ExportColumn[] = [
    { header: 'Rep', role: 'rep', get: (r: any) => r.rep },
    ...CATS.map(c => ({ header: (byRep?.category_labels?.[c] || c).split(' / ')[0], money: true, get: (r: any) => r[c] || 0 })),
    { header: 'Ledger payout', money: true, get: (r: any) => r.ledger_payout || 0 },
    { header: 'Live payout', money: true, get: (r: any) => r.live_payout ?? 0 },
  ]
  // RULE FIVE rep(s) picker (By-rep view). The ledger data model carries only rep_user + amounts — no
  // store/market — so ONLY the rep dimension is meaningful here (store/market omitted, see handoff note).
  const repRowsAll: RepRow[] = byRep?.reps || []
  const repOpts = useMemo(() => optionsFromRows(repRowsAll, { rep: (r: any) => r.rep }).reps, [repRowsAll])
  const filteredReps: RepRow[] = selReps.length ? repRowsAll.filter(r => selReps.includes(r.rep)) : repRowsAll
  // Totals row + export reflect the FILTERED set (WYSIWYG §3c).
  const repTotals: Record<string, number> = useMemo(() => {
    if (!selReps.length) return byRep?.totals || {}
    const t: Record<string, number> = { ledger_payout: 0, live_payout: 0 }
    CATS.forEach(c => { t[c] = 0 })
    filteredReps.forEach(r => {
      t.ledger_payout += r.ledger_payout || 0; t.live_payout += (r.live_payout || 0)
      CATS.forEach(c => { t[c] += (r as any)[c] || 0 })
    })
    return t
  }, [byRep, selReps, filteredReps])
  const canExport = summ && summ.line_count > 0
  const exportProps = view === 'cat'
    ? { title: `Commission Ledger — ${src}${period ? ' — ' + period : ''}`, filename: `commission_ledger_${src}`, columns: catExportCols, rows: catExportRows }
    : { title: `Commission Ledger by rep — ${src}${period ? ' — ' + period : ''}`, filename: `commission_ledger_by_rep_${src}`, columns: repExportCols, rows: filteredReps }

  return (
    <div style={{ padding: 24, maxWidth: 1100 }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, marginBottom: 4 }}>🧾 Commission Ledger</h1>
      <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 13, marginBottom: 10 }}>
        Any carrier's commission file → five canonical buckets, classified once and displayed as it's paid.
      </p>
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', marginBottom: 12,
        background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 10, padding: '10px 14px' }}>
        <b style={{ fontSize: 13 }}>First time?</b>
        <span style={{ fontSize: 13, color: 'var(--text2)' }}>
          1. Pick your carrier → 2. Upload the file → 3. Check the preview → 4. Import.
        </span>
        <a href="/commcalc/commission-ledger/setup" style={{ marginLeft: 'auto', padding: '7px 14px', borderRadius: 8, background: 'var(--accent,#2563eb)', color: '#fff', fontSize: 13, fontWeight: 600, textDecoration: 'none' }}>🧭 Open setup wizard</a>
      </div>
      <p style={{ color: 'var(--text3)', fontSize: 12, marginBottom: 10 }}>
        Already set up? Pick a template + period below and import directly. Adjust the rules on{' '}
        <a href="/commcalc/commission-category-map" style={{ color: 'var(--accent,#2563eb)' }}>Commission Category Map →</a>
      </p>

      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', marginBottom: 14 }}>
        <label style={{ fontSize: 13, color: 'var(--text2)' }}>Template</label>
        <select style={inp} value={src} onChange={e => setSrc(e.target.value)}>
          {tmpls.map(t => <option key={t.key} value={t.key}>{t.label}{t.rule_count ? ` (${t.rule_count} rules)` : ''}</option>)}
        </select>
        <input style={{ ...inp, width: 130 }} placeholder="Period e.g. June 2026" value={period} onChange={e => setPeriod(e.target.value)} />
        <label style={{ ...inp, cursor: busy ? 'wait' : 'pointer', fontWeight: 600 }}>
          {busy ? 'Working…' : '⬆ Import file'}
          <input type="file" accept=".xls,.xlsx,.csv,.txt" style={{ display: 'none' }} disabled={busy}
            onChange={e => { const f = e.target.files?.[0]; if (f) upload(f); e.currentTarget.value = '' }} />
        </label>
        {/* Refresh the ledger from the raw MA tables that already flow (raw_ma_daily_tx /
            raw_ma_commission) instead of waiting on a hand-uploaded file. Preview first — this button
            writes nothing. */}
        {tmpl?.ma_syncable && (
          <button onClick={previewSync} disabled={busy || !syncReady}
            title={syncReady ? `Derive this period's ledger from ${(tmpl.ma_sources || []).join(' + ')} — preview first, nothing is written`
              : `Needs migration ${syncMig} — the ledger's provenance column`}
            style={{ ...inp, cursor: syncReady ? 'pointer' : 'not-allowed', fontWeight: 600,
              opacity: syncReady ? 1 : 0.55 }}>
            🔄 Refresh from MA data
          </button>
        )}
        {provOrigins.length > 1 && (
          <select style={inp} value={origin} onChange={e => setOrigin(e.target.value)} aria-label="Filter by source">
            <option value="">Both sources</option>
            {provOrigins.map(o => <option key={o.origin} value={o.origin}>{o.label} only</option>)}
          </select>
        )}
      </div>
      {msg && <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, padding: '8px 12px', fontSize: 13, marginBottom: 12 }}>{msg}</div>}

      {/* ── WHERE THIS PERIOD'S NUMBERS CAME FROM, AND WHEN (so "stale" is visible, not silent) ── */}
      {period && prov && (
        <div style={{ border: '1px solid var(--border)', borderRadius: 10, padding: '10px 14px', marginBottom: 12, background: 'var(--surface)', fontSize: 13 }}>
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'baseline' }}>
            <b style={{ fontSize: 12, textTransform: 'uppercase', color: 'var(--text3)' }}>Source of {period}</b>
            {provOrigins.length === 0
              ? <span style={{ color: 'var(--text3)' }}>nothing imported or synced for this template yet</span>
              : provOrigins.map(o => (
                <span key={o.origin} style={{ padding: '2px 8px', borderRadius: 6, border: '1px solid var(--border)' }}>
                  {o.label}: <b>{o.lines.toLocaleString()}</b> lines · {money(o.payout_total)}
                  {o.last_at ? <span style={{ color: 'var(--text3)' }}> · last {String(o.last_at).slice(0, 16).replace('T', ' ')}</span> : null}
                </span>
              ))}
            {provPeriod?.raw_rows ? (
              <span style={{ color: 'var(--text3)' }}>
                raw MA feed holds {provPeriod.raw_rows.toLocaleString()} row(s) for this period
                {Object.keys(provPeriod.raw_available || {}).length ? ` (${Object.entries(provPeriod.raw_available).map(([t, n]) => `${t} ${n}`).join(', ')})` : ''}
                {prov.raw_sources?.some(s => s.truncated) ? ' — feed scan hit its row cap, so this count is a floor' : ''}
              </span>
            ) : null}
          </div>
          {provPeriod?.overlap && (
            <div style={{ marginTop: 6, color: '#9a3412' }}>
              ⚠️ Two sources populated this period — the tiles below <b>add them together</b>. Use the source
              filter above to read one at a time.
            </div>
          )}
          {provPeriod?.stale && (
            <div style={{ marginTop: 6, color: '#9a3412' }}>
              ⚠️ The raw MA feed has data for this period that has <b>never been synced</b> into the ledger —
              &ldquo;Refresh from MA data&rdquo; brings it in.
            </div>
          )}
          {!prov.ready && (
            <div style={{ marginTop: 6, color: 'var(--text3)' }}>
              Provenance is unavailable until migration <b>{prov.migration}</b> runs — every existing line is
              shown as a file import (which is what they all are).
            </div>
          )}
        </div>
      )}

      {/* ── REFRESH PREVIEW: what a sync WOULD write. Nothing is saved until Apply is clicked. ── */}
      {prev && (
        <div style={{ border: '1px solid var(--accent,#2563eb)', borderRadius: 10, padding: 14, marginBottom: 14, background: 'var(--surface)' }}>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', marginBottom: 8 }}>
            <b style={{ fontSize: 14 }}>
              {prev.saved != null ? `✅ Refreshed ${prev.saved.toLocaleString()} line(s) from MA data` : `Preview — ${prev.would_write.toLocaleString()} line(s) would be written`}
            </b>
            <span style={{ fontSize: 12, color: 'var(--text3)' }}>
              {period} · {prev.sources.map(s => s.source_table).join(' + ')}
            </span>
            <div style={{ flex: 1 }} />
            {prev.saved == null && (
              <button onClick={applySync} disabled={applying || !prev.would_write || !prev.ready}
                style={{ ...inp, cursor: prev.would_write && prev.ready ? 'pointer' : 'not-allowed', fontWeight: 700,
                  background: prev.would_write && prev.ready ? 'var(--accent,#2563eb)' : 'var(--surface)',
                  color: prev.would_write && prev.ready ? '#fff' : 'var(--text3)' }}>
                {applying ? 'Writing…' : `Apply — replace the MA-synced rows for ${period}`}
              </button>
            )}
            <button onClick={() => setPrev(null)} style={{ ...inp, cursor: 'pointer', fontSize: 11 }}>✕ close</button>
          </div>

          {prev.overlap_note && (
            <div style={{ background: '#fff7ed', border: '1px solid #fdba74', color: '#9a3412', borderRadius: 8, padding: '8px 12px', fontSize: 13, marginBottom: 8 }}>
              {prev.overlap_note}
            </div>
          )}

          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 10 }}>
            {CATS.map(c => (
              <div key={c} style={{ ...tile, minWidth: 130, padding: 10 }}>
                <div style={{ fontSize: 10, color: 'var(--text3)', textTransform: 'uppercase' }}>{labels[c] || c}</div>
                <div style={{ fontSize: 16, fontWeight: 700 }}>{money(prev.summary?.categories?.[c]?.total || 0)}</div>
                <div style={{ fontSize: 10, color: 'var(--text3)' }}>{prev.summary?.categories?.[c]?.count || 0} lines</div>
              </div>
            ))}
            <div style={{ ...tile, minWidth: 130, padding: 10 }}>
              <div style={{ fontSize: 10, color: 'var(--text3)', textTransform: 'uppercase' }}>Total payouts</div>
              <div style={{ fontSize: 16, fontWeight: 700 }}>{money(prev.summary?.payout_total || 0)}</div>
              <div style={{ fontSize: 10, color: 'var(--text3)' }}>{money(prev.summary?.charge_total || 0)} bill/act.</div>
            </div>
          </div>

          {/* every honesty counter — a line that is not written is COUNTED and named, never dropped */}
          <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 8 }}>
            Read {prev.guard.rows_in.toLocaleString()} raw row(s) → {prev.guard.lines_out.toLocaleString()} ledger line(s)
            {prev.guard.skipped_empty_amount ? ` · ${prev.guard.skipped_empty_amount.toLocaleString()} zero-amount component(s) skipped` : ''}
            {prev.guard.skipped_no_content ? ` · ${prev.guard.skipped_no_content.toLocaleString()} row(s) with no usable content` : ''}
            {prev.guard.excluded_ceiling ? ` · ⛔ ${prev.guard.excluded_ceiling.toLocaleString()} line(s) over the sanity ceiling (${money(prev.guard.excluded_ceiling_total)}) EXCLUDED` : ''}
          </div>
          {prev.guard.excluded_ceiling > 0 && (
            <div style={{ background: '#fef2f2', border: '1px solid #fca5a5', color: '#991b1b', borderRadius: 8, padding: '8px 12px', fontSize: 12, marginBottom: 8 }}>
              <b>Excluded as implausible</b> (an ID-like value read as dollars is the failure this catches):{' '}
              {prev.guard.excluded_examples.slice(0, 5).map((x, i) => (
                <span key={i}>{i ? ' · ' : ''}{x.source_table}.{x.column} = {money(x.amount)}{x.label ? ` (${x.label})` : ''}</span>
              ))}
            </div>
          )}
          {prev.guard.refused?.length > 0 && prev.guard.refused.map((r, i) => (
            <div key={i} style={{ background: '#fef2f2', border: '1px solid #fca5a5', color: '#991b1b', borderRadius: 8, padding: '8px 12px', fontSize: 12, marginBottom: 8 }}>
              ⛔ {r.source_table}: {r.reason}
            </div>
          ))}
          {prev.unmapped?.length > 0 && (
            <div style={{ background: '#fff7ed', border: '1px solid #fdba74', color: '#9a3412', borderRadius: 8, padding: '8px 12px', fontSize: 12, marginBottom: 8 }}>
              ⚠️ {prev.unmapped.length} label(s) match no rule and would land in <b>Other</b>:{' '}
              {prev.unmapped.slice(0, 8).map((u: any, i: number) => <span key={i}>{i ? ' · ' : ''}<b>{u.product_name || '(blank)'}</b> {money(u.payout_total)}</span>)}
              {' — '}<a href={`/commcalc/commission-category-map?source_report=${encodeURIComponent(src)}`} style={{ color: '#9a3412', textDecoration: 'underline' }}>add a rule on the Category Map</a> and preview again.
            </div>
          )}

          {/* per-source mapping transparency: which raw column fed each ledger field, at what confidence */}
          {prev.sources.map(s => (
            <details key={s.report_key} style={{ fontSize: 12, marginTop: 6 }}>
              <summary style={{ cursor: 'pointer' }}>
                <b>{s.source_table}</b> — {s.read.rows.toLocaleString()} row(s)
                {s.read.matched_by ? ` matched by ${s.read.matched_by}` : ' (no rows matched)'} · {s.kind} shape · mapping from {s.column_map_source}
                {s.diag.amount_col ? ` · amount = ${s.diag.amount_col} (${s.diag.amount_confidence})` : ''}
              </summary>
              <div style={{ padding: '6px 0 0 12px', color: 'var(--text2)' }}>
                {s.mapped_fields.map(f => (
                  <div key={f.target_field}>{f.label} ← <code>{s.source_table}.{f.col}</code>{' '}
                    <span style={{ color: 'var(--text3)' }}>(via header &ldquo;{f.header}&rdquo;, {f.confidence})</span></div>
                ))}
                {s.synthesized_fields?.length > 0 && (
                  <div style={{ color: 'var(--text3)' }}>synthesized per component: {s.synthesized_fields.join(', ')}</div>
                )}
                {s.unresolved_fields.map(u => (
                  <div key={u.target_field} style={{ color: '#9a3412' }}>{u.label} — no column here (header &ldquo;{u.header}&rdquo; absent) → left empty</div>
                ))}
                {s.components?.length > 0 && (
                  <div style={{ marginTop: 4 }}>
                    components: {s.components.map(c => `${c.label}${c.payment_month ? ` (M${c.payment_month})` : ''}`).join(' · ')}
                  </div>
                )}
                {s.read.truncated && <div style={{ color: '#9a3412' }}>⚠️ read was capped at 100,000 rows</div>}
                {s.read.error && <div style={{ color: '#9a3412' }}>read error: {s.read.error}</div>}
              </div>
            </details>
          ))}
          {prev.warnings?.length > 0 && (
            <details style={{ fontSize: 12, marginTop: 6 }}>
              <summary style={{ cursor: 'pointer', color: '#9a3412' }}>{prev.warnings.length} note(s) about this refresh</summary>
              <ul style={{ margin: '6px 0 0 18px' }}>{prev.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
            </details>
          )}
        </div>
      )}

      {!summ || summ.line_count === 0 ? (
        <div style={{ color: 'var(--text3)', fontSize: 13 }}>No ledger data for this template/period yet — import a file above.</div>
      ) : (
        <>
          {summ.other_count > 0 && (
            <div style={{ background: '#fff7ed', border: '1px solid #fdba74', color: '#9a3412', borderRadius: 8, padding: '8px 12px', fontSize: 13, marginBottom: 12 }}>
              ⚠️ {summ.other_count} payout line(s) totaling {money(summ.other_total)} are <b>unmapped</b> — add a rule on the Category Map so they land in a bucket.
            </div>
          )}
          <div style={{ display: 'flex', gap: 6, marginBottom: 14, alignItems: 'center' }}>
            {(['cat', 'rep'] as const).map(v => (
              <button key={v} onClick={() => setView(v)} style={{ ...inp, cursor: 'pointer', fontWeight: view === v ? 700 : 400,
                background: view === v ? 'var(--accent,#2563eb)' : 'var(--surface)', color: view === v ? '#fff' : 'inherit' }}>
                {v === 'cat' ? 'By category' : 'By rep'}
              </button>
            ))}
            {view === 'rep' && repOpts.length > 0 && (
              <EntityPicker multi options={repOpts} value={selReps} onChange={setSelReps} placeholder="Reps…" width={190} ariaLabel="Filter by rep" />
            )}
            <div style={{ flex: 1 }} />
            {canExport && (view === 'cat' ? catExportRows.length > 0 : (byRep?.reps?.length || 0) > 0) && <ReportExportBar {...exportProps} />}
          </div>

          {view === 'cat' && (<>
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 18 }}>
            {CATS.map(c => (
              <div key={c} style={{ ...tile, cursor: 'pointer' }} onClick={() => openDrill(c)} title="Click to drill">
                <div style={{ fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase', marginBottom: 4 }}>{labels[c] || c}</div>
                <div style={{ fontSize: 20, fontWeight: 700 }}>{money(summ.categories[c]?.total || 0)}</div>
                <div style={{ fontSize: 11, color: 'var(--text3)' }}>{summ.categories[c]?.count || 0} lines</div>
              </div>
            ))}
            <div style={{ ...tile, background: 'var(--bg, transparent)' }}>
              <div style={{ fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase', marginBottom: 4 }}>Total payouts</div>
              <div style={{ fontSize: 20, fontWeight: 700 }}>{money(summ.payout_total)}</div>
              <div style={{ fontSize: 11, color: 'var(--text3)' }}>{summ.line_count} lines · {money(summ.charge_total)} bill/act. payments</div>
            </div>
          </div>

          {/* ── COMMISSION LEG (owner 2026-08-04): 1st Month vs M2–M12, the SAME money as above ──
              This is a decomposition, not a second total: each row of the table sums back to the
              category total in the tiles. Rules come from the same Category → Bucket Map. */}
          {summ.legs && (
            <div style={{ border: '1px solid var(--border)', borderRadius: 10, padding: 12, marginBottom: 18 }}>
              <div style={{ display: 'flex', gap: 10, alignItems: 'baseline', flexWrap: 'wrap', marginBottom: 8 }}>
                <b style={{ fontSize: 13 }}>🧩 Commission legs — 1st Month vs M2–M12</b>
                <span style={{ fontSize: 12, color: 'var(--text3)' }}>{summ.leg_basis}</span>
                <div style={{ flex: 1 }} />
                <a href="/commcalc/commission-category-map" style={{ fontSize: 12, color: 'var(--accent,#2563eb)' }}>Set a label&apos;s leg →</a>
              </div>
              {summ.leg_identity_ok === false && (
                <div style={{ background: '#fff7ed', border: '1px solid #fdba74', color: '#9a3412', borderRadius: 8, padding: '6px 10px', fontSize: 12, marginBottom: 8 }}>
                  The legs below do not add back to the category totals — treat the split as unreliable and report it.
                </div>
              )}
              <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 10 }}>
                {LEG_ORDER.filter(b => (summ.legs?.[b] || 0) !== 0 || b !== 'unsplit').map(b => (
                  <div key={b} style={{ ...tile }}>
                    <div style={{ fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase', marginBottom: 4 }}>{summ.leg_labels?.[b] || LEG_FALLBACK[b]}</div>
                    <div style={{ fontSize: 20, fontWeight: 700 }}>{money(summ.legs?.[b] || 0)}</div>
                    <div style={{ fontSize: 11, color: 'var(--text3)' }}>
                      {summ.payout_total ? `${Math.round((summ.legs?.[b] || 0) / summ.payout_total * 1000) / 10}% of payouts` : '—'}
                    </div>
                  </div>
                ))}
              </div>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                <thead>
                  <tr style={{ textAlign: 'right', color: 'var(--text3)', fontSize: 11 }}>
                    <th style={{ textAlign: 'left', padding: '6px 8px' }}>Category</th>
                    {LEG_ORDER.map(b => <th key={b} style={{ padding: '6px 8px' }}>{summ.leg_labels?.[b] || LEG_FALLBACK[b]}</th>)}
                    <th style={{ padding: '6px 8px' }}>Total</th>
                  </tr>
                </thead>
                <tbody>
                  {[...CATS, 'other'].map(c => {
                    const row = summ.by_category_leg?.[c] || {}
                    const tot = c === 'other' ? (summ.other_total || 0) : (summ.categories[c]?.total || 0)
                    if (!tot && !LEG_ORDER.some(b => row[b])) return null
                    return (
                      <tr key={c} style={{ borderTop: '1px solid var(--border)' }}>
                        <td style={{ padding: '6px 8px', fontWeight: 600 }}>{labels[c] || (c === 'other' ? 'Other (unmapped)' : c)}</td>
                        {LEG_ORDER.map(b => <td key={b} style={{ padding: '6px 8px', textAlign: 'right', color: row[b] ? 'inherit' : 'var(--text3)' }}>{row[b] ? money(row[b]) : '·'}</td>)}
                        <td style={{ padding: '6px 8px', textAlign: 'right', fontWeight: 600 }}>{money(tot)}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
              {(summ.leg_unmapped_total || 0) > 0 && (
                <div style={{ fontSize: 12, color: '#9a3412', marginTop: 8, lineHeight: 1.6 }}>
                  <b>{money(summ.leg_unmapped_total || 0)}</b> could not be attributed to a leg — its source never
                  states a month-of-life. Top labels:{' '}
                  {(summ.leg_unmapped || []).slice(0, 6).map((u, i) => <span key={u.label}>{i ? ' · ' : ''}<b>{u.label}</b> {money(u.amount)}</span>)}
                  {'. '}Give them a leg on{' '}
                  <a href="/commcalc/commission-category-map" style={{ color: 'var(--accent,#2563eb)' }}>Category → Bucket Map</a>.
                </div>
              )}
            </div>
          )}

          {months.length > 0 && (
            <>
              <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text3)', textTransform: 'uppercase', marginBottom: 6 }}>By payment month</div>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13, marginBottom: 18 }}>
                <thead>
                  <tr style={{ textAlign: 'right', color: 'var(--text3)', fontSize: 11 }}>
                    <th style={{ textAlign: 'left', padding: '6px 8px' }}>Category</th>
                    {months.map(m => <th key={m} style={{ padding: '6px 8px' }}>M{m}</th>)}
                    <th style={{ padding: '6px 8px' }}>Total</th>
                  </tr>
                </thead>
                <tbody>
                  {CATS.map(c => (
                    <tr key={c} style={{ borderTop: '1px solid var(--border)' }}>
                      <td style={{ padding: '6px 8px', fontWeight: 600 }}>{labels[c] || c}</td>
                      {months.map(m => <td key={m} style={{ padding: '6px 8px', textAlign: 'right', color: cell(c, m) ? 'inherit' : 'var(--text3)' }}>{cell(c, m) ? money(cell(c, m)) : '·'}</td>)}
                      <td style={{ padding: '6px 8px', textAlign: 'right', fontWeight: 600 }}>{money(summ.categories[c]?.total || 0)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}

          {drill && (
            <div style={{ border: '1px solid var(--border)', borderRadius: 10, padding: 12 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
                <b>{labels[drill.cat] || drill.cat} — {drill.rows.length} lines</b>
                <button onClick={() => setDrill(null)} style={{ ...inp, cursor: 'pointer', fontSize: 11 }}>✕ close</button>
              </div>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                <thead><tr style={{ textAlign: 'left', color: 'var(--text3)', fontSize: 11 }}>
                  <th style={{ padding: '4px 6px' }}>Rep</th><th style={{ padding: '4px 6px' }}>Product</th>
                  <th style={{ padding: '4px 6px' }}>Mo</th><th style={{ padding: '4px 6px' }}>Date</th>
                  <th style={{ padding: '4px 6px', textAlign: 'right' }}>Payout</th></tr></thead>
                <tbody>
                  {drill.rows.slice(0, 300).map((r, i) => (
                    <tr key={i} style={{ borderTop: '1px solid var(--border)' }}>
                      <td style={{ padding: '4px 6px' }}>{r.rep_user}</td>
                      <td style={{ padding: '4px 6px' }}>{r.product_name}</td>
                      <td style={{ padding: '4px 6px' }}>{r.payment_month || ''}</td>
                      <td style={{ padding: '4px 6px' }}>{r.trans_date}</td>
                      <td style={{ padding: '4px 6px', textAlign: 'right' }}>{money(r.payout_total)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          </>)}

          {view === 'rep' && (
            !byRep ? <div style={{ color: 'var(--text3)', fontSize: 13 }}>Loading…</div> :
            byRep.reps.length === 0 ? <div style={{ color: 'var(--text3)', fontSize: 13 }}>No rep-attributed ledger payouts for this template/period.</div> : (
            <>
              <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 8 }}>
                Each rep&apos;s canonical-ledger payout, with what the live calc actually pays them
                (<a href={`/commcalc/commissions/${encodeURIComponent(period)}`} style={{ color: 'var(--accent,#2563eb)' }}>rep commissions</a>) alongside.
                {' '}{byRep.matched_count}/{byRep.rep_count} reps matched to a live payout{period ? '' : ' — enter a period to join the live payout'}.
              </div>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                <thead>
                  <tr style={{ textAlign: 'right', color: 'var(--text3)', fontSize: 11 }}>
                    <th style={{ textAlign: 'left', padding: '6px 8px' }}>Rep</th>
                    {CATS.map(c => <th key={c} style={{ padding: '6px 8px' }}>{(byRep.category_labels?.[c] || c).split(' / ')[0]}</th>)}
                    <th style={{ padding: '6px 8px' }}>Ledger payout</th>
                    <th style={{ padding: '6px 8px' }}>Live payout</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredReps.map((r, i) => (
                    <tr key={i} style={{ borderTop: '1px solid var(--border)' }}>
                      <td style={{ padding: '6px 8px', fontWeight: 600 }}>{r.rep}</td>
                      {CATS.map(c => <td key={c} style={{ padding: '6px 8px', textAlign: 'right', color: r[c] ? 'inherit' : 'var(--text3)' }}>{r[c] ? money(r[c]) : '·'}</td>)}
                      <td style={{ padding: '6px 8px', textAlign: 'right', fontWeight: 600 }}>{money(r.ledger_payout)}</td>
                      <td style={{ padding: '6px 8px', textAlign: 'right' }}
                          title={r.matched ? 'from rep_commissions.total_payout' : 'no matching live rep payout for this period'}>
                        {r.live_payout == null ? <span style={{ color: 'var(--text3)' }}>—</span> : money(r.live_payout)}
                      </td>
                    </tr>
                  ))}
                  {filteredReps.length === 0 && (
                    <tr><td colSpan={CATS.length + 3} style={{ padding: 16, textAlign: 'center', color: 'var(--text3)' }}>No reps match the selected rep filter.</td></tr>
                  )}
                  <tr style={{ borderTop: '2px solid var(--border)', fontWeight: 700 }}>
                    <td style={{ padding: '6px 8px' }}>Total{selReps.length ? ` (${filteredReps.length} of ${repRowsAll.length})` : ''}</td>
                    {CATS.map(c => <td key={c} style={{ padding: '6px 8px', textAlign: 'right' }}>{money(repTotals[c] || 0)}</td>)}
                    <td style={{ padding: '6px 8px', textAlign: 'right' }}>{money(repTotals.ledger_payout || 0)}</td>
                    <td style={{ padding: '6px 8px', textAlign: 'right' }}>{money(repTotals.live_payout || 0)}</td>
                  </tr>
                </tbody>
              </table>
            </>)
          )}
        </>
      )}
    </div>
  )
}
