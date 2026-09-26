'use client'
// FRANCHISE ROYALTY REPORT (owner 2026-09-25, mig 1022, index §37). One report per center per month: sales →
// exclusions → commissions → sales subject to royalty → fees due. Import the franchisor's page (PDF / saved HTML /
// pasted text) or type it in; every figure the report prints is STORED AS PRINTED, and every cent the configured fee
// rule disagrees with is FLAGGED beside it — never corrected.
//
// Nothing on this page names a franchisor, a vertical or a line: the line vocabulary, fee rates, which fee absorbs the
// rounding remainder, and what each line books to on the P&L all come from GET /account/royalty/config (per-org config
// over a house default). Every endpoint is gated by the `royalty` module server-side.
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, apiUpload, fmt } from '@/lib/client'
import { useReportKinds } from '@/lib/report-kinds'
import ShowsIn from '@/components/ShowsIn'

const th: React.CSSProperties = { textAlign: 'left', padding: '6px 9px', fontSize: 12, color: 'var(--text2)', borderBottom: '1px solid var(--border)', whiteSpace: 'nowrap' }
const thr: React.CSSProperties = { ...th, textAlign: 'right' }
const td: React.CSSProperties = { padding: '6px 9px', fontSize: 13, borderBottom: '1px solid var(--border)', verticalAlign: 'top' }
const tdr: React.CSSProperties = { ...td, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }
// A figure as the API sends it (a stored-as-printed amount may arrive as a number or a decimal string).
type Money = number | string | null | undefined
const money = (v: Money) => (v == null || v === '' ? '—' : fmt(v as number))
const SECTION_LABEL: Record<string, string> = { sales: 'Products / services', exclusion: 'Exclusions', commission: 'Commissions', str: 'Subject to royalty', fee: 'Fees due' }
type Tab = 'reports' | 'import' | 'batch' | 'manual' | 'setup'

// The fields of the /account/royalty/* payloads this page reads.
interface Flag { code: string; message: string; expected?: Money; reported?: Money; diff?: number | null }
interface RoyaltyLine {
  section: string; line_key: string; label: string; role?: string
  amount?: Money; adjustment?: Money; adjusted_amount?: Money; reason?: string | null
  rate?: number | null; pl_line_key?: string | null; pl_note?: string | null; absorbs_remainder?: boolean
  daily_categories?: string[]; _source?: string
}
interface PlCoverage {
  booked?: Record<string, Money>
  excluded?: Record<string, { label: string; section: string; amount: number; reason: string }>
  unmapped?: Record<string, { label: string; amount: number; why: string }>
}
interface ReportSummary {
  id: string; period: string; center_code: string; store_ref?: string | null; status?: string
  total_gross_sales?: Money; total_adjusted_str?: Money; total_str?: Money; total_due?: Money
}
interface ReportDetail extends ReportSummary {
  total_exclusions_adjusted?: Money; total_commissions_adjusted?: Money
  validation?: { flags?: Flag[] }; lines?: RoyaltyLine[]
}
interface ReportOpen { report?: ReportDetail; pl_coverage?: PlCoverage }
interface RoyaltyParse {
  parsed: { center?: string; period_label?: string; lines: RoyaltyLine[] }; source?: string
  validation: { flags: Flag[] }; pl_coverage?: PlCoverage; store_note?: string
}
interface RoyaltyConf { fee_basis?: string; daily_source?: string; daily_match_field?: string; book_pl?: boolean; lookback_months?: number }
interface RoyaltyConfig {
  lines?: RoyaltyLine[]; config?: RoyaltyConf; pl_lines?: { key: string; label: string; section: string }[]
  daily_sources?: string[]; match_fields?: string[]; problems?: string[]
}

function Card({ title, note, children }: { title: string; note?: React.ReactNode; children?: React.ReactNode }) {
  return (
    <div className="card" style={{ padding: 14, marginBottom: 14 }}>
      <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 4 }}>{title}</div>
      {note && <div style={{ fontSize: 12.5, color: 'var(--text2)', marginBottom: 8 }}>{note}</div>}
      {children}
    </div>
  )
}

function Flags({ flags }: { flags: Flag[] }) {
  if (!flags?.length) return <div style={{ color: '#15803d', fontSize: 13 }}>Every check passed — the sections add up, the STR is gross sales − exclusions + commissions, and each fee matches the rule.</div>
  return (
    <table style={{ width: '100%', borderCollapse: 'collapse' }}>
      <thead><tr><th style={th}>Check</th><th style={th}>What differs</th><th style={thr}>By the rule</th><th style={thr}>On the report</th><th style={thr}>Difference</th></tr></thead>
      <tbody>{flags.map((f, i) => (
        <tr key={i}><td style={td}><code style={{ fontSize: 11 }}>{f.code}</code></td><td style={td}>{f.message}</td>
          <td style={tdr}>{money(f.expected)}</td><td style={tdr}>{money(f.reported)}</td>
          <td style={{ ...tdr, color: f.diff ? '#b91c1c' : undefined }}>{f.diff == null ? '—' : fmt(f.diff)}</td></tr>
      ))}</tbody>
    </table>
  )
}

function Lines({ lines }: { lines: RoyaltyLine[] }) {
  const bySec = useMemo(() => {
    const m: Record<string, RoyaltyLine[]> = {}
    for (const l of lines || []) (m[l.section] = m[l.section] || []).push(l)
    return m
  }, [lines])
  return (
    <>{Object.keys(SECTION_LABEL).filter(s => bySec[s]?.length).map(s => (
      <div key={s} style={{ marginBottom: 10 }}>
        <div style={{ fontWeight: 600, fontSize: 13, margin: '6px 0' }}>{SECTION_LABEL[s]}</div>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead><tr><th style={th}>Line</th><th style={thr}>Amount</th><th style={thr}>Adjustment</th><th style={thr}>Adjusted</th><th style={th}>Reason</th></tr></thead>
          <tbody>{bySec[s].map(l => (
            <tr key={l.section + l.line_key} style={l.role && l.role !== 'line' ? { fontWeight: 600 } : undefined}>
              <td style={td}>{l.label}{String(l.line_key || '').startsWith('unknown_') && <span style={{ color: '#b45309' }}> (not in the line vocabulary)</span>}</td>
              <td style={tdr}>{money(l.amount)}</td><td style={tdr}>{money(l.adjustment)}</td><td style={tdr}>{money(l.adjusted_amount)}</td>
              <td style={td}>{l.reason || ''}</td></tr>
          ))}</tbody>
        </table>
      </div>
    ))}</>
  )
}

function Coverage({ cov }: { cov: PlCoverage | null | undefined }) {
  if (!cov) return null
  const booked = Object.entries(cov.booked || {})
  const excl = Object.entries(cov.excluded || {})
  const unm = Object.entries(cov.unmapped || {})
  return (
    <div style={{ fontSize: 13 }}>
      <div style={{ marginBottom: 6 }}><b>Books to the P&L:</b> {booked.length ? booked.map(([k, v]) => `${k} ${fmt(Number(v))}`).join(' · ') : 'nothing'}</div>
      {excl.length > 0 && <div style={{ marginBottom: 6 }}><b>Books nothing, on purpose:</b>{excl.map(([k, e]) => <div key={k} style={{ color: 'var(--text2)' }}>{e.label} ({SECTION_LABEL[e.section] || e.section}) {fmt(e.amount)} — {e.reason}</div>)}</div>}
      {unm.length > 0 && <div style={{ color: '#b91c1c' }}><b>Unmapped — books nothing until a P&L line is chosen under Line setup:</b>{unm.map(([k, u]) => <div key={k}>{u.label} {fmt(u.amount)} — {u.why}</div>)}</div>}
    </div>
  )
}

export default function RoyaltyReportPage() {
  const [tab, setTab] = useState<Tab>('reports')
  const [cfg, setCfg] = useState<RoyaltyConfig | null>(null)
  const [reports, setReports] = useState<ReportSummary[]>([])
  const [sel, setSel] = useState<ReportOpen | null>(null)
  const [err, setErr] = useState('')
  const kinds = useReportKinds()

  const loadCfg = useCallback(() => api('/api/v1/account/royalty/config').then(setCfg).catch(e => setErr(e?.message || String(e))), [])
  const loadReports = useCallback(() => api('/api/v1/account/royalty/reports').then(d => setReports(d.reports || [])).catch(e => setErr(e?.message || String(e))), [])
  useEffect(() => { loadCfg(); loadReports() }, [loadCfg, loadReports])

  const open = (id: string) => api(`/api/v1/account/royalty/report/${id}`).then(setSel).catch(e => setErr(e?.message || String(e)))

  return (
    <div style={{ padding: 16, maxWidth: 1200 }}>
      <h1 style={{ fontSize: 20, fontWeight: 700, margin: '0 0 4px' }}>Franchise Royalty Report</h1>
      <div style={{ fontSize: 13, color: 'var(--text2)', marginBottom: 8 }}>
        One report per center per month. The figures are stored exactly as the report prints them; anything the fee rule or the report&apos;s own totals disagree with is flagged, never changed.
      </div>
      <ShowsIn info={kinds.showsIn('royalty_report')} loaded={kinds.loaded} />
      {err && <div style={{ color: '#b91c1c', margin: '8px 0' }}>{err}</div>}
      {(cfg?.problems?.length ?? 0) > 0 && cfg?.problems && <div style={{ color: '#b45309', margin: '8px 0' }}>Line setup needs attention: {cfg.problems.join(' · ')}</div>}
      <div style={{ display: 'flex', gap: 6, margin: '10px 0 14px', flexWrap: 'wrap' }}>
        {(['reports', 'import', 'batch', 'manual', 'setup'] as Tab[]).map(t => (
          <button key={t} className={tab === t ? 'btn btn-primary' : 'btn'} onClick={() => setTab(t)}>
            {{ reports: 'Reports', import: 'Import a report', batch: 'Upload several months', manual: 'Enter by hand', setup: 'Line setup' }[t]}
          </button>
        ))}
      </div>
      {tab === 'reports' && <ReportsTab reports={reports} open={open} sel={sel} onDeleted={() => { setSel(null); loadReports() }} />}
      {tab === 'import' && <ImportTab onSaved={() => { loadReports(); setTab('reports') }} />}
      {tab === 'batch' && <BatchTab onSaved={loadReports} />}
      {tab === 'manual' && cfg && <ManualTab cfg={cfg} onSaved={() => { loadReports(); setTab('reports') }} />}
      {tab === 'setup' && cfg && <SetupTab cfg={cfg} reload={loadCfg} />}
    </div>
  )
}

function ReportsTab({ reports, open, sel, onDeleted }: { reports: ReportSummary[]; open: (id: string) => void; sel: ReportOpen | null; onDeleted: () => void }) {
  const r = sel?.report
  return (
    <>
      <Card title="Stored reports" note={reports.length ? undefined : 'No report yet — import one or enter it by hand.'}>
        {reports.length > 0 && (
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr><th style={th}>Period</th><th style={th}>Center</th><th style={th}>Store</th><th style={thr}>Gross sales</th><th style={thr}>STR</th><th style={thr}>Total due</th><th style={th}>Checks</th><th style={th}></th></tr></thead>
            <tbody>{reports.map(x => (
              <tr key={x.id}><td style={td}>{x.period}</td><td style={td}>{x.center_code}</td>
                <td style={td}>{x.store_ref || <span style={{ color: '#b45309' }}>no store — books company-wide</span>}</td>
                <td style={tdr}>{money(x.total_gross_sales)}</td><td style={tdr}>{money(x.total_adjusted_str ?? x.total_str)}</td><td style={tdr}>{money(x.total_due)}</td>
                <td style={td}>{x.status === 'flagged' ? <span style={{ color: '#b91c1c' }}>flagged</span> : <span style={{ color: '#15803d' }}>ok</span>}</td>
                <td style={td}><button className="btn" onClick={() => open(x.id)}>Open</button></td></tr>
            ))}</tbody>
          </table>
        )}
      </Card>
      {r && (
        <Card title={`Center ${r.center_code} — ${r.period}`} note={r.store_ref ? `Books to store ${r.store_ref}.` : 'No store is mapped to this center — its lines book company-wide. Map the store under Profit Centers, then re-import.'}>
          <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap', fontSize: 13, marginBottom: 10 }}>
            <span>Gross sales <b>{money(r.total_gross_sales)}</b></span><span>Exclusions <b>{money(r.total_exclusions_adjusted)}</b></span>
            <span>Commissions <b>{money(r.total_commissions_adjusted)}</b></span><span>STR <b>{money(r.total_str)}</b></span>
            <span>Adjusted STR <b>{money(r.total_adjusted_str)}</b></span><span>Total due <b>{money(r.total_due)}</b></span>
          </div>
          <div style={{ fontWeight: 600, fontSize: 13, margin: '8px 0 4px' }}>Checks</div>
          <Flags flags={r.validation?.flags || []} />
          <div style={{ fontWeight: 600, fontSize: 13, margin: '12px 0 4px' }}>On the P&L</div>
          <Coverage cov={sel?.pl_coverage} />
          <div style={{ fontWeight: 600, fontSize: 13, margin: '12px 0 4px' }}>Lines as printed</div>
          <Lines lines={r.lines || []} />
          <button className="btn" style={{ marginTop: 8 }} onClick={() => {
            if (!window.confirm('Delete this stored report? Its lines leave the P&L for that month.')) return
            api(`/api/v1/account/royalty/report/${r.id}`, { method: 'DELETE' }).then(onDeleted)
          }}>Delete this report</button>
        </Card>
      )}
    </>
  )
}

function ImportTab({ onSaved }: { onSaved: () => void }) {
  const [file, setFile] = useState<File | null>(null)
  const [text, setText] = useState('')
  const [center, setCenter] = useState('')
  const [period, setPeriod] = useState('')
  const [store, setStore] = useState('')
  const [preview, setPreview] = useState<RoyaltyParse | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const form = () => {
    const f = new FormData()
    if (file) f.append('file', file)
    if (text.trim()) f.append('text', text)
    f.append('center', center); f.append('period', period); f.append('store_ref', store)
    return f
  }
  const run = (path: string, then: (d: RoyaltyParse) => void) => {
    setBusy(true); setErr('')
    apiUpload(path, form()).then(then).catch(e => setErr(e?.message || String(e))).finally(() => setBusy(false))
  }
  return (
    <Card title="Import the report" note="Save the franchisor's Submit Royalty page as a PDF or as HTML (File → Save page), or copy the whole page and paste it below. Preview first: nothing is saved until you press Save.">
      <div style={{ display: 'grid', gap: 8, maxWidth: 720 }}>
        <input type="file" accept=".pdf,.html,.htm,.txt,text/html,application/pdf,text/plain" onChange={e => setFile(e.target.files?.[0] || null)} />
        <textarea rows={8} placeholder="…or paste the page's text here" value={text} onChange={e => setText(e.target.value)} style={{ width: '100%', fontFamily: 'monospace', fontSize: 12 }} />
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <input placeholder="Center (read from the report if blank)" value={center} onChange={e => setCenter(e.target.value)} />
          <input placeholder="Period, e.g. June 2026 (read if blank)" value={period} onChange={e => setPeriod(e.target.value)} />
          <input placeholder="Store (found through the profit center if blank)" value={store} onChange={e => setStore(e.target.value)} />
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn" disabled={busy || (!file && !text.trim())} onClick={() => run('/api/v1/account/royalty/parse', setPreview)}>Preview</button>
          <button className="btn btn-primary" disabled={busy || (!file && !text.trim())} onClick={() => run('/api/v1/account/royalty/import', d => {
            if (d.store_note) window.alert(`Saved. ${d.store_note}`)
            onSaved()
          })}>Save</button>
        </div>
        {err && <div style={{ color: '#b91c1c' }}>{err}</div>}
      </div>
      {preview && (
        <div style={{ marginTop: 12 }}>
          <div style={{ fontSize: 13, marginBottom: 6 }}>Read: center <b>{preview.parsed.center || '—'}</b>, period <b>{preview.parsed.period_label || '—'}</b>, {preview.parsed.lines.length} lines ({preview.source}).</div>
          <Flags flags={preview.validation.flags} />
          <div style={{ margin: '10px 0 4px', fontWeight: 600, fontSize: 13 }}>On the P&L</div>
          <Coverage cov={preview.pl_coverage} />
          <Lines lines={preview.parsed.lines} />
        </div>
      )}
    </Card>
  )
}

function ManualTab({ cfg, onSaved }: { cfg: RoyaltyConfig; onSaved: () => void }) {
  const [vals, setVals] = useState<Record<string, { amount?: string; adjustment?: string; reason?: string }>>({})
  const [center, setCenter] = useState('')
  const [period, setPeriod] = useState('')
  const [store, setStore] = useState('')
  const [res, setRes] = useState<{ status?: string; validation?: { flags?: Flag[] } } | null>(null)
  const [err, setErr] = useState('')
  const rows = (cfg.lines || []).filter(l => l.role !== 'header' && l.role !== 'echo')
  const set = (k: string, f: string, v: string) => setVals(p => ({ ...p, [k]: { ...(p[k] || {}), [f]: v } }))
  const save = () => {
    setErr('')
    const entries = Object.entries(vals).filter(([, v]) => v.amount !== undefined && v.amount !== '')
      .map(([k, v]) => ({ line_key: k, amount: Number(v.amount), adjustment: v.adjustment === undefined || v.adjustment === '' ? null : Number(v.adjustment), reason: v.reason || null }))
    api('/api/v1/account/royalty/manual', { method: 'POST', body: JSON.stringify({ center_code: center, period, store_ref: store || null, entries }) })
      .then(d => { setRes(d); if (d.status === 'ok') onSaved() }).catch(e => setErr(e?.message || String(e)))
  }
  return (
    <Card title="Enter the report by hand" note="Type each figure as the report prints it. Leave a total blank and it is added up from the lines — and recorded as added up, not printed.">
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 10 }}>
        <input placeholder="Center" value={center} onChange={e => setCenter(e.target.value)} />
        <input placeholder="Period, e.g. June 2026" value={period} onChange={e => setPeriod(e.target.value)} />
        <input placeholder="Store (optional)" value={store} onChange={e => setStore(e.target.value)} />
      </div>
      <table style={{ width: '100%', borderCollapse: 'collapse' }}>
        <thead><tr><th style={th}>Section</th><th style={th}>Line</th><th style={thr}>Amount</th><th style={thr}>Adjustment</th><th style={th}>Reason</th></tr></thead>
        <tbody>{rows.map(l => (
          <tr key={l.line_key} style={l.role !== 'line' ? { fontWeight: 600 } : undefined}>
            <td style={td}>{SECTION_LABEL[l.section] || l.section}</td><td style={td}>{l.label}{l.rate != null ? ` (${(l.rate * 100).toFixed(2).replace(/\.?0+$/, '')}%)` : ''}</td>
            <td style={tdr}><input style={{ width: 110, textAlign: 'right' }} inputMode="decimal" value={vals[l.line_key]?.amount || ''} onChange={e => set(l.line_key, 'amount', e.target.value)} /></td>
            <td style={tdr}>{(l.section === 'exclusion' || l.section === 'commission') && l.role === 'line' && <input style={{ width: 90, textAlign: 'right' }} inputMode="decimal" value={vals[l.line_key]?.adjustment || ''} onChange={e => set(l.line_key, 'adjustment', e.target.value)} />}</td>
            <td style={td}>{(l.section === 'exclusion' || l.section === 'commission') && l.role === 'line' && <input value={vals[l.line_key]?.reason || ''} onChange={e => set(l.line_key, 'reason', e.target.value)} />}</td>
          </tr>
        ))}</tbody>
      </table>
      <button className="btn btn-primary" style={{ marginTop: 10 }} onClick={save}>Save</button>
      {err && <div style={{ color: '#b91c1c', marginTop: 8 }}>{err}</div>}
      {res && <div style={{ marginTop: 10 }}><Flags flags={res.validation?.flags || []} /></div>}
    </Card>
  )
}

function SetupTab({ cfg, reload }: { cfg: RoyaltyConfig; reload: () => void }) {
  const [err, setErr] = useState('')
  const [conf, setConf] = useState<RoyaltyConf>(cfg.config || {})
  const plOptions = (cfg.pl_lines || []) as { key: string; label: string; section: string }[]
  const save = (row: RoyaltyLine, patch: Partial<RoyaltyLine>) => {
    setErr('')
    api('/api/v1/account/royalty/lines', { method: 'PUT', body: JSON.stringify({ line_key: row.line_key, ...patch }) })
      .then(reload).catch(e => setErr(e?.message || String(e)))
  }
  const saveConf = () => api('/api/v1/account/royalty/config', { method: 'PUT', body: JSON.stringify(conf) }).then(reload).catch(e => setErr(e?.message || String(e)))
  return (
    <>
      <Card title="How the report is read and reconciled">
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', fontSize: 13, alignItems: 'center' }}>
          <label>Fees are charged on <select value={conf.fee_basis} onChange={e => setConf({ ...conf, fee_basis: e.target.value })}>
            <option value="adjusted_str">the adjusted STR</option><option value="str">the STR</option></select></label>
          <label>Daily report table <select value={conf.daily_source} onChange={e => setConf({ ...conf, daily_source: e.target.value })}>
            {(cfg.daily_sources || []).map((s: string) => <option key={s} value={s}>{s}</option>)}</select></label>
          <label>Match daily rows on <select value={conf.daily_match_field} onChange={e => setConf({ ...conf, daily_match_field: e.target.value })}>
            {(cfg.match_fields || []).map((s: string) => <option key={s} value={s}>{s}</option>)}</select></label>
          <label><input type="checkbox" checked={conf.book_pl !== false} onChange={e => setConf({ ...conf, book_pl: e.target.checked })} /> The report books the P&L</label>
          <label>Several-months upload reaches back <input type="number" min={1} max={120} style={{ width: 64 }} value={conf.lookback_months ?? ''}
            onChange={e => setConf({ ...conf, lookback_months: e.target.value === '' ? undefined : Number(e.target.value) })} /> months</label>
          <button className="btn" onClick={saveConf}>Save</button>
        </div>
      </Card>
      <Card title="Line vocabulary" note="Each line the report prints: the names it goes by, the P&L line it books to (or why it books nothing), each fee's rate and the ONE fee that takes the rounding remainder, and the daily-report categories a sales line is reconciled against. A change here is your company's own row; the default is untouched.">
        {err && <div style={{ color: '#b91c1c', marginBottom: 8 }}>{err}</div>}
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead><tr><th style={th}>Section</th><th style={th}>Line</th><th style={th}>Books to</th><th style={th}>Why it books nothing</th><th style={th}>Rate</th><th style={th}>Remainder</th><th style={th}>Daily categories</th></tr></thead>
          <tbody>{(cfg.lines || []).filter(l => l.role === 'line').map(l => (
            <tr key={l.line_key}>
              <td style={td}>{SECTION_LABEL[l.section] || l.section}</td>
              <td style={td}>{l.label}{l._source !== 'house' && <span style={{ color: 'var(--text3)' }}> · yours</span>}</td>
              <td style={td}><select value={l.pl_line_key || ''} onChange={e => save(l, { pl_line_key: e.target.value || null })}>
                <option value="">— nothing —</option>
                {plOptions.map(o => <option key={o.key} value={o.key}>{o.label} ({o.section})</option>)}</select></td>
              <td style={td}><input defaultValue={l.pl_note || ''} placeholder={l.pl_line_key ? '' : 'no reason → reported as unmapped'} onBlur={e => e.target.value !== (l.pl_note || '') && save(l, { pl_note: e.target.value || null })} /></td>
              <td style={td}>{l.section === 'fee' && <input style={{ width: 70 }} defaultValue={l.rate ?? ''} onBlur={e => save(l, { rate: e.target.value === '' ? null : Number(e.target.value) })} />}</td>
              <td style={td}>{l.section === 'fee' && <input type="checkbox" checked={!!l.absorbs_remainder} onChange={e => save(l, { absorbs_remainder: e.target.checked })} />}</td>
              <td style={td}>{l.section === 'sales' && <input defaultValue={(l.daily_categories || []).join(', ')} placeholder="same name as the line" onBlur={e => save(l, { daily_categories: e.target.value.split(',').map(s => s.trim()).filter(Boolean) })} />}</td>
            </tr>
          ))}</tbody>
        </table>
      </Card>
    </>
  )
}

// ── MANY MONTHS IN ONE GO (owner 2026-09-26, index §37.10) ───────────────────────────────────────────────
// Pick or drop several monthly reports → the backend reads EACH through the single import's own parser, takes the
// center and month from the report's OWN header, runs the same checks, and says whether that center × month is already
// on file (it will be replaced) or why the file cannot land. Tick the rows → Import selected → each ticked file lands
// through the single import's own writer, one at a time; a file that fails never stops the others. The lookback window
// and every month shown come from the API (per-company setting) — nothing here works a month out.
interface CoverageMonth { period: string; on_file: { center_code: string; id: string; status?: string; total_due?: Money }[]; missing: string[] }
interface CoverageInfo { lookback_months: number; window: string[]; centers: string[]; months: CoverageMonth[] }
interface BatchFile {
  index: number; file_name: string; center?: string | null; period?: string | null; period_label?: string | null
  status?: string; flags?: Flag[]; total_due?: Money; lines?: number; error?: string | null
  refusals: string[]; ready: boolean; replace: boolean; existing?: { id: string; total_due?: Money; status?: string } | null
}
interface BatchPreview { files: BatchFile[]; ready: number; coverage: CoverageInfo }
interface BatchResultRow {
  index: number; file_name: string; center_code?: string | null; period?: string | null; ok: boolean; error?: string
  replaced?: boolean; status?: string; flags?: number; store_note?: string | null; total_due?: Money
}
interface BatchResult { results: BatchResultRow[]; imported: number; failed: number; sentence: string; coverage: CoverageInfo }

const fileKey = (f: File) => `${f.name}|${f.size}|${f.lastModified}`
// 'September 2024' → 'Sep 2024' for a narrow cell (the API's canonical spelling, shortened — never re-derived)
const shortMonth = (p: string) => { const [m, y] = p.split(' '); return `${(m || '').slice(0, 3)} ${y || ''}` }

function CoverageStrip({ cov, inUpload }: { cov: CoverageInfo | null; inUpload: Set<string> }) {
  if (!cov) return <div style={{ fontSize: 13, color: 'var(--text2)' }}>Loading which months are on file…</div>
  const nC = cov.centers.length
  const tone = (m: CoverageMonth) => m.on_file.length === 0 ? { bg: 'var(--surface2, #f3f4f6)', fg: 'var(--text2)', word: 'missing' }
    : m.missing.length === 0 ? { bg: '#dcfce7', fg: '#15803d', word: 'on file' } : { bg: '#fef3c7', fg: '#b45309', word: 'partly on file' }
  const have = cov.months.filter(m => m.on_file.length > 0 && m.missing.length === 0).length
  return (
    <div>
      <div style={{ fontSize: 12.5, color: 'var(--text2)', marginBottom: 6 }}>
        {have} of {cov.months.length} months fully on file ({cov.window[0]} – {cov.window[cov.window.length - 1]}, this month and the {cov.lookback_months} before it
        {nC ? ` · ${nC} center${nC === 1 ? '' : 's'}: ${cov.centers.join(', ')}` : ' · no report on file yet'}).
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
        {cov.months.map(m => {
          const t = tone(m)
          const tip = `${m.period}: ${m.on_file.length ? 'on file for ' + m.on_file.map(x => x.center_code).join(', ') : 'no report'}`
            + (m.on_file.length && m.missing.length ? ` · missing ${m.missing.join(', ')}` : '') + (inUpload.has(m.period) ? ' · in this upload' : '')
          return (
            <div key={m.period} title={tip} style={{ minWidth: 64, padding: '5px 6px', borderRadius: 6, background: t.bg, color: t.fg, fontSize: 11.5,
              textAlign: 'center', border: inUpload.has(m.period) ? '2px solid var(--accent, #2563eb)' : '1px solid var(--border)' }}>
              <div style={{ fontWeight: 600 }}>{shortMonth(m.period)}</div>
              <div>{t.word}</div>
            </div>
          )
        })}
      </div>
      <div style={{ fontSize: 11.5, color: 'var(--text2)', marginTop: 6 }}>Green = every center on file · amber = some centers missing · grey = missing · blue outline = in the files you picked.</div>
    </div>
  )
}

function BatchTab({ onSaved }: { onSaved: () => void }) {
  const [files, setFiles] = useState<File[]>([])
  const [preview, setPreview] = useState<BatchPreview | null>(null)
  const [checked, setChecked] = useState<Set<number>>(new Set())
  const [cov, setCov] = useState<CoverageInfo | null>(null)
  const [result, setResult] = useState<BatchResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [drag, setDrag] = useState(false)
  const [err, setErr] = useState('')

  useEffect(() => { api('/api/v1/account/royalty/coverage').then(setCov).catch(e => setErr(e?.message || String(e))) }, [])

  const form = (fs: File[]) => { const f = new FormData(); fs.forEach(x => f.append('files', x)); return f }
  const runPreview = (fs: File[]) => {
    setFiles(fs); setResult(null); setErr('')
    if (!fs.length) { setPreview(null); setChecked(new Set()); return }
    setBusy(true)
    apiUpload('/api/v1/account/royalty/batch/preview', form(fs)).then((d: BatchPreview) => {
      setPreview(d); setCov(d.coverage)
      setChecked(new Set(d.files.filter(r => r.ready).map(r => r.index)))
    }).catch(e => setErr(e?.message || String(e))).finally(() => setBusy(false))
  }
  const add = (list: FileList | null) => {
    if (!list?.length) return
    const seen = new Set(files.map(fileKey))
    runPreview([...files, ...Array.from(list).filter(f => !seen.has(fileKey(f)))])
  }
  const remove = (i: number) => runPreview(files.filter((_, j) => j !== i))
  const toggle = (i: number) => setChecked(p => { const n = new Set(p); if (n.has(i)) n.delete(i); else n.add(i); return n })
  const importSelected = () => {
    const pick = files.filter((_, i) => checked.has(i))
    if (!pick.length) return
    const replacing = (preview?.files || []).filter(r => checked.has(r.index) && r.replace).length
    if (replacing && !window.confirm(`${replacing} of the selected report(s) will REPLACE a report already on file for that center and month. Continue?`)) return
    setBusy(true); setErr('')
    apiUpload('/api/v1/account/royalty/batch/import', form(pick)).then((d: BatchResult) => {
      setResult(d); setCov(d.coverage); setPreview(null); setFiles([]); setChecked(new Set()); onSaved()
    }).catch(e => setErr(e?.message || String(e))).finally(() => setBusy(false))
  }
  const rows = preview?.files || []
  const readyRows = rows.filter(r => r.ready)
  const inUpload = new Set(rows.filter(r => r.ready && r.period).map(r => r.period as string))
  const allOn = readyRows.length > 0 && readyRows.every(r => checked.has(r.index))

  return (
    <>
      <Card title="Months on file" note="Which months of the lookback window already hold a royalty report. The window is a company setting (Line setup).">
        <CoverageStrip cov={cov} inUpload={inUpload} />
      </Card>
      <Card title="Upload several months at once" note={`Pick or drop the monthly royalty reports (PDF, saved HTML or text) — several months and several centers at once, up to ${cov ? cov.lookback_months : '…'} months back. Each report's center and month are read from its own header. Nothing is saved until you press Import selected.`}>
        <div onDragOver={e => { e.preventDefault(); setDrag(true) }} onDragLeave={() => setDrag(false)}
          onDrop={e => { e.preventDefault(); setDrag(false); add(e.dataTransfer.files) }}
          style={{ border: `2px dashed ${drag ? 'var(--accent, #2563eb)' : 'var(--border)'}`, borderRadius: 10, padding: 18, textAlign: 'center', fontSize: 13, color: 'var(--text2)', marginBottom: 10 }}>
          Drag the report files here, or{' '}
          <label className="btn" style={{ cursor: busy ? 'default' : 'pointer' }}>
            choose files
            <input type="file" multiple accept=".pdf,.html,.htm,.txt,text/html,application/pdf,text/plain" style={{ display: 'none' }} disabled={busy}
              onChange={e => { add(e.target.files); e.target.value = '' }} />
          </label>
          {files.length > 0 && <div style={{ marginTop: 6 }}>{files.length} file(s) picked{busy ? ' — reading…' : ''}</div>}
        </div>
        {err && <div style={{ color: '#b91c1c', marginBottom: 8 }}>{err}</div>}
        {rows.length > 0 && (
          <>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead><tr>
                <th style={th}><input type="checkbox" aria-label="Select every ready file" checked={allOn} disabled={!readyRows.length}
                  onChange={() => setChecked(allOn ? new Set() : new Set(readyRows.map(r => r.index)))} /></th>
                <th style={th}>File</th><th style={th}>Month</th><th style={th}>Center</th><th style={thr}>Total due</th><th style={th}>Checks</th>
                <th style={th}>New / replace</th><th style={th}>Why it cannot be imported</th><th style={th}></th>
              </tr></thead>
              <tbody>{rows.map(r => (
                <tr key={r.index} style={r.ready ? undefined : { background: '#fef2f2' }}>
                  <td style={td}><input type="checkbox" aria-label={`Import ${r.file_name}`} checked={checked.has(r.index)} disabled={!r.ready} onChange={() => toggle(r.index)} /></td>
                  <td style={td}>{r.file_name}</td>
                  <td style={td}>{r.period || <span style={{ color: '#b91c1c' }}>not read{r.period_label ? ` (“${r.period_label}”)` : ''}</span>}</td>
                  <td style={td}>{r.center || <span style={{ color: '#b91c1c' }}>not read</span>}</td>
                  <td style={tdr}>{money(r.total_due)}</td>
                  <td style={td}>{r.error ? '—' : r.flags?.length ? <span style={{ color: '#b45309' }} title={r.flags.map(f => f.message).join('\n')}>{r.flags.length} flag(s) — saved as printed</span> : <span style={{ color: '#15803d' }}>ok</span>}</td>
                  <td style={td}>{!r.ready ? '—' : r.replace ? <span style={{ color: '#b45309' }}>will REPLACE the report on file{r.existing?.total_due != null ? ` (total due ${money(r.existing.total_due)})` : ''}</span> : 'new'}</td>
                  <td style={{ ...td, color: '#b91c1c' }}>{r.refusals.join(' · ')}</td>
                  <td style={td}><button className="btn" disabled={busy} onClick={() => remove(r.index)}>Remove</button></td>
                </tr>
              ))}</tbody>
            </table>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 10, flexWrap: 'wrap' }}>
              <button className="btn btn-primary" disabled={busy || checked.size === 0} onClick={importSelected}>Import selected ({checked.size})</button>
              <span style={{ fontSize: 12.5, color: 'var(--text2)' }}>{readyRows.length} of {rows.length} file(s) can be imported. A file that cannot be imported is shown in red with the reason — remove it (or fix it and pick it again).</span>
            </div>
          </>
        )}
      </Card>
      {result && (
        <Card title="Import results" note={result.sentence}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr><th style={th}>File</th><th style={th}>Result</th><th style={th}>Month</th><th style={th}>Center</th><th style={thr}>Total due</th><th style={th}>Notes</th></tr></thead>
            <tbody>{result.results.map(r => (
              <tr key={r.index}>
                <td style={td}>{r.file_name}</td>
                <td style={td}>{r.ok ? <span style={{ color: '#15803d' }}>imported{r.replaced ? ' (replaced the report on file)' : ''}</span> : <span style={{ color: '#b91c1c' }}>NOT imported</span>}</td>
                <td style={td}>{r.period || '—'}</td><td style={td}>{r.center_code || '—'}</td><td style={tdr}>{money(r.total_due)}</td>
                <td style={td}>{r.ok ? [r.flags ? `${r.flags} flag(s) — open it under Reports` : '', r.store_note || ''].filter(Boolean).join(' · ') : <span style={{ color: '#b91c1c' }}>{r.error}</span>}</td>
              </tr>
            ))}</tbody>
          </table>
        </Card>
      )}
    </>
  )
}
