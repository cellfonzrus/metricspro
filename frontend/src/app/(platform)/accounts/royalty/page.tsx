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
const money = (v: any) => (v == null || v === '' ? '—' : fmt(v))
const SECTION_LABEL: Record<string, string> = { sales: 'Products / services', exclusion: 'Exclusions', commission: 'Commissions', str: 'Subject to royalty', fee: 'Fees due' }
type Tab = 'reports' | 'import' | 'manual' | 'setup'

function Card({ title, note, children }: { title: string; note?: React.ReactNode; children?: React.ReactNode }) {
  return (
    <div className="card" style={{ padding: 14, marginBottom: 14 }}>
      <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 4 }}>{title}</div>
      {note && <div style={{ fontSize: 12.5, color: 'var(--text2)', marginBottom: 8 }}>{note}</div>}
      {children}
    </div>
  )
}

function Flags({ flags }: { flags: any[] }) {
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

function Lines({ lines }: { lines: any[] }) {
  const bySec = useMemo(() => {
    const m: Record<string, any[]> = {}
    for (const l of lines || []) (m[l.section] = m[l.section] || []).push(l)
    return m
  }, [lines])
  return (
    <>{Object.keys(SECTION_LABEL).filter(s => bySec[s]?.length).map(s => (
      <div key={s} style={{ marginBottom: 10 }}>
        <div style={{ fontWeight: 600, fontSize: 13, margin: '6px 0' }}>{SECTION_LABEL[s]}</div>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead><tr><th style={th}>Line</th><th style={thr}>Amount</th><th style={thr}>Adjustment</th><th style={thr}>Adjusted</th><th style={th}>Reason</th></tr></thead>
          <tbody>{bySec[s].map((l: any) => (
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

function Coverage({ cov }: { cov: any }) {
  if (!cov) return null
  const booked = Object.entries(cov.booked || {})
  const excl = Object.entries(cov.excluded || {}) as [string, any][]
  const unm = Object.entries(cov.unmapped || {}) as [string, any][]
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
  const [cfg, setCfg] = useState<any>(null)
  const [reports, setReports] = useState<any[]>([])
  const [sel, setSel] = useState<any>(null)
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
      {cfg?.problems?.length > 0 && <div style={{ color: '#b45309', margin: '8px 0' }}>Line setup needs attention: {cfg.problems.join(' · ')}</div>}
      <div style={{ display: 'flex', gap: 6, margin: '10px 0 14px', flexWrap: 'wrap' }}>
        {(['reports', 'import', 'manual', 'setup'] as Tab[]).map(t => (
          <button key={t} className={tab === t ? 'btn btn-primary' : 'btn'} onClick={() => setTab(t)}>
            {{ reports: 'Reports', import: 'Import a report', manual: 'Enter by hand', setup: 'Line setup' }[t]}
          </button>
        ))}
      </div>
      {tab === 'reports' && <ReportsTab reports={reports} open={open} sel={sel} onDeleted={() => { setSel(null); loadReports() }} />}
      {tab === 'import' && <ImportTab onSaved={() => { loadReports(); setTab('reports') }} />}
      {tab === 'manual' && cfg && <ManualTab cfg={cfg} onSaved={() => { loadReports(); setTab('reports') }} />}
      {tab === 'setup' && cfg && <SetupTab cfg={cfg} reload={loadCfg} />}
    </div>
  )
}

function ReportsTab({ reports, open, sel, onDeleted }: { reports: any[]; open: (id: string) => void; sel: any; onDeleted: () => void }) {
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
          <Coverage cov={sel.pl_coverage} />
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
  const [preview, setPreview] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const form = () => {
    const f = new FormData()
    if (file) f.append('file', file)
    if (text.trim()) f.append('text', text)
    f.append('center', center); f.append('period', period); f.append('store_ref', store)
    return f
  }
  const run = (path: string, then: (d: any) => void) => {
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

function ManualTab({ cfg, onSaved }: { cfg: any; onSaved: () => void }) {
  const [vals, setVals] = useState<Record<string, { amount?: string; adjustment?: string; reason?: string }>>({})
  const [center, setCenter] = useState('')
  const [period, setPeriod] = useState('')
  const [store, setStore] = useState('')
  const [res, setRes] = useState<any>(null)
  const [err, setErr] = useState('')
  const rows = (cfg.lines || []).filter((l: any) => l.role !== 'header' && l.role !== 'echo')
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
        <tbody>{rows.map((l: any) => (
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

function SetupTab({ cfg, reload }: { cfg: any; reload: () => void }) {
  const [err, setErr] = useState('')
  const [conf, setConf] = useState<any>(cfg.config || {})
  const plOptions = (cfg.pl_lines || []) as { key: string; label: string; section: string }[]
  const save = (row: any, patch: any) => {
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
          <button className="btn" onClick={saveConf}>Save</button>
        </div>
      </Card>
      <Card title="Line vocabulary" note="Each line the report prints: the names it goes by, the P&L line it books to (or why it books nothing), each fee's rate and the ONE fee that takes the rounding remainder, and the daily-report categories a sales line is reconciled against. A change here is your company's own row; the default is untouched.">
        {err && <div style={{ color: '#b91c1c', marginBottom: 8 }}>{err}</div>}
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead><tr><th style={th}>Section</th><th style={th}>Line</th><th style={th}>Books to</th><th style={th}>Why it books nothing</th><th style={th}>Rate</th><th style={th}>Remainder</th><th style={th}>Daily categories</th></tr></thead>
          <tbody>{(cfg.lines || []).filter((l: any) => l.role === 'line').map((l: any) => (
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
