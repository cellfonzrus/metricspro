'use client'
import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import Link from 'next/link'
import { api, apiUpload } from '@/lib/client'
import { apiCached, LOOKUP } from '@/lib/cache'
import EntityPicker from '@/components/EntityPicker'
import { LastUploadLine, useLastUploads } from '../_lib/lastUpload'

// Per-carrier MANUAL upload for the MA reports — the SAP-style parallel track to the flaky live portal
// pull (owner directive 2026-07-17). Wizard: pick a carrier → pick a report (mapping status shown) →
// map a sample ONCE if needed (inherits the report_pull default otherwise) → upload data, HISTORICAL
// (one file spanning many months) or APPEND (dedup-and-add). INGEST-ONLY — nothing here recomputes pay.

const sel: React.CSSProperties = { padding: '6px 8px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const cell: React.CSSProperties = { padding: '6px 8px', borderTop: '1px solid var(--border)', fontSize: 13 }
const STEPS = ['Carrier', 'Report', 'Map columns', 'Upload data']

type ReportInfo = {
  report_key: string; display_name: string; target_table: string; calibration: boolean
  mapped: boolean; source: 'saved' | 'default' | 'none'; columns: number; saved_at?: string | null
  saved_by?: string | null; date_field?: string; dedup_keys: string[]; join_note?: string | null
}

export default function MaManualUpload() {
  const [step, setStep] = useState(0)
  const [msg, setMsg] = useState('')

  const [carriers, setCarriers] = useState<any[]>([])
  const [carrierId, setCarrierId] = useState('')
  const [reports, setReports] = useState<ReportInfo[]>([])
  const [reportKey, setReportKey] = useState('')

  const loadCarriers = useCallback(() => apiCached('/api/v1/commcalc/carriers', LOOKUP).then((c: any) => setCarriers(c || [])).catch(() => {}), [])
  useEffect(() => { loadCarriers() }, [loadCarriers])

  const loadReports = useCallback(() => {
    if (!carrierId) { setReports([]); return }
    api(`/api/v1/commcalc/manual-upload/reports?carrier_id=${encodeURIComponent(carrierId)}`)
      .then((d: any) => setReports(d?.reports || [])).catch((e: any) => setMsg('❌ ' + (e?.message || e)))
  }, [carrierId])
  useEffect(() => { loadReports() }, [loadReports])

  const report = reports.find(r => r.report_key === reportKey) || null
  const carrier = carriers.find(c => c.id === carrierId) || null

  return (
    <div>
      <div style={{ marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>⬆️ Manual report upload</h1>
        <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          Upload MA report files by hand — <b>per carrier</b>, against a saved column mapping. This is the
          parallel track to the automated portal pull. <b>Ingest only</b> — uploading never recomputes anyone&apos;s pay.
        </p>
      </div>

      {/* stepper */}
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 16 }}>
        {STEPS.map((s, i) => (
          <button key={s} onClick={() => setStep(i)} className="card" style={{
            padding: '6px 10px', fontSize: 12, cursor: 'pointer', border: '1px solid var(--border)',
            fontWeight: i === step ? 700 : 400, background: i === step ? 'var(--surface2)' : 'var(--surface)',
            color: i < step ? '#16794a' : 'inherit' }}>
            {i < step ? '✓ ' : `${i + 1}. `}{s}
          </button>
        ))}
      </div>

      <div className="card" style={{ padding: 18, minHeight: 220 }}>
        {/* STEP 1 — Carrier */}
        {step === 0 && (
          <div>
            <h3 style={{ marginTop: 0 }}>1. Choose the carrier</h3>
            <p style={{ fontSize: 13, color: 'var(--text2)' }}>Uploads are divided per carrier to keep them unambiguous. Pick which carrier this file belongs to.</p>
            <EntityPicker
              options={carriers.map((c: any) => ({ id: c.id, label: c.name, sublabel: c.code || undefined }))}
              value={carrierId || null} width={320}
              onChange={(v) => { setCarrierId(v || ''); setReportKey('') }}
              placeholder="Carrier…" ariaLabel="Carrier" />
            {carriers.length === 0 && <p style={{ fontSize: 12, color: 'var(--text3)', marginTop: 8 }}>No carriers configured for this tenant yet — add one under Onboarding → Carrier.</p>}
          </div>
        )}

        {/* STEP 2 — Report */}
        {step === 1 && (
          <div>
            <h3 style={{ marginTop: 0 }}>2. Choose the report {carrier ? <>for <b>{carrier.name}</b></> : ''}</h3>
            {!carrierId && <p style={{ fontSize: 13, color: 'var(--text3)' }}>Pick a carrier in step 1 first.</p>}
            <div style={{ display: 'grid', gap: 8, maxWidth: 680 }}>
              {reports.map(r => (
                <button key={r.report_key} onClick={() => { setReportKey(r.report_key); setStep(r.mapped ? 3 : 2) }}
                  className="card" style={{ textAlign: 'left', padding: 12, cursor: 'pointer',
                    border: reportKey === r.report_key ? '2px solid var(--primary,#2563eb)' : '1px solid var(--border)' }}>
                  <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                    <b style={{ fontSize: 14 }}>{r.display_name}</b>
                    <MapBadge r={r} />
                    {r.calibration && <span style={{ fontSize: 11, color: '#b45309' }}>· calibration</span>}
                    <div style={{ flex: 1 }} />
                    <span style={{ fontSize: 11, color: 'var(--text3)' }}>→ {r.target_table}</span>
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 4 }}>
                    Dedup key: {r.dedup_keys.join(' + ') || '(full row)'}
                    {r.join_note ? ` · linked by ${r.join_note}` : ''}
                  </div>
                </button>
              ))}
              {carrierId && reports.length === 0 && <p style={{ fontSize: 13, color: 'var(--text3)' }}>No report types configured. Add them under 🗺️ Report mapping.</p>}
            </div>
          </div>
        )}

        {/* STEP 3 — Map columns */}
        {step === 2 && (
          report
            ? <MapStep carrierId={carrierId} report={report} setMsg={setMsg} onSaved={() => { loadReports(); setStep(3) }} />
            : <p style={{ fontSize: 13, color: 'var(--text3)' }}>Pick a report in step 2 first.</p>
        )}

        {/* STEP 4 — Upload data */}
        {step === 3 && (
          report
            ? <UploadStep carrierId={carrierId} report={report} setMsg={setMsg} onRemap={() => setStep(2)} />
            : <p style={{ fontSize: 13, color: 'var(--text3)' }}>Pick a report in step 2 first.</p>
        )}
      </div>

      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginTop: 14 }}>
        <button className="btn btn-secondary" disabled={step === 0} onClick={() => setStep(s => Math.max(0, s - 1))}>← Back</button>
        <button className="btn btn-primary" disabled={step === STEPS.length - 1 || (step === 0 && !carrierId) || (step === 1 && !reportKey)}
          onClick={() => setStep(s => Math.min(STEPS.length - 1, s + 1))}>Next →</button>
        <Link href="/commcalc/email-imports#portal-logins" style={{ fontSize: 13 }}>← Data Imports</Link>
        {msg && <span style={{ fontSize: 13, marginLeft: 'auto' }}>{msg}</span>}
      </div>
    </div>
  )
}

function MapBadge({ r }: { r: ReportInfo }) {
  if (r.source === 'saved') return <span style={{ fontSize: 11, color: '#16794a' }}>✓ mapped ({r.columns} cols · saved {r.saved_at ? new Date(r.saved_at).toLocaleDateString() : ''}{r.saved_by ? ` by ${r.saved_by}` : ''})</span>
  if (r.source === 'default') return <span style={{ fontSize: 11, color: '#0369a1' }}>✓ mapped ({r.columns} cols · default)</span>
  return <span style={{ fontSize: 11, color: '#b91c1c' }}>needs mapping</span>
}

// ── Step 3: sample → detect → map columns → save (per org,carrier,report_key) ────────────────────
function MapStep({ carrierId, report, setMsg, onSaved }: { carrierId: string; report: ReportInfo; setMsg: (s: string) => void; onSaved: () => void }) {
  const [headers, setHeaders] = useState<string[]>([])
  const [fields, setFields] = useState<{ col: string; type: string; default_source?: string }[]>([])
  const [src, setSrc] = useState<Record<string, string>>({})
  const [detected, setDetected] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  // preload the effective mapping so an already-mapped report shows its fields even before a sample
  useEffect(() => {
    api(`/api/v1/commcalc/manual-upload/mapping?report_key=${encodeURIComponent(report.report_key)}&carrier_id=${encodeURIComponent(carrierId)}`)
      .then((d: any) => { setFields(d?.target_fields || []); setHeaders(d?.sample_headers || []) }).catch(() => {})
  }, [report.report_key, carrierId])

  async function detect(file: File) {
    setBusy(true)
    const fd = new FormData(); fd.append('report_key', report.report_key); fd.append('carrier_id', carrierId); fd.append('file', file)
    try {
      const d: any = await apiUpload('/api/v1/commcalc/manual-upload/detect', fd)
      setHeaders(d?.headers || []); setFields(d?.target_fields || []); setDetected(d?.detected_periods || [])
      setSrc(prev => ({ ...(d?.suggestions || {}), ...prev }))
      setMsg(`🔍 Detected ${d?.headers?.length || 0} columns, ${d?.rows_in || 0} rows${(d?.detected_periods || []).length ? `, months ${(d.detected_periods).join(', ')}` : ''}.`)
    } catch (e: any) { setMsg('❌ Sample read failed: ' + (e?.message || e)) } finally { setBusy(false) }
  }

  async function save() {
    const field_sources: Record<string, string> = {}
    for (const f of fields) { const s = (src[f.col] || '').trim(); if (s) field_sources[f.col] = s }
    if (Object.keys(field_sources).length === 0) { setMsg('⚠️ Map at least one column before saving.'); return }
    try {
      const r: any = await api('/api/v1/commcalc/manual-upload/mapping', { method: 'POST', body: JSON.stringify({ report_key: report.report_key, carrier_id: carrierId, field_sources, sample_headers: headers }) })
      setMsg(`✅ Saved mapping (${r?.columns ?? Object.keys(field_sources).length} columns). You can now upload data files against it.`)
      onSaved()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }

  async function useDefault() {
    // "keep the built-in mapping" — nothing to save; the report_pull default already maps this report.
    setMsg('✅ Using the built-in default mapping. Go straight to Upload data.'); onSaved()
  }

  return (
    <div>
      <h3 style={{ marginTop: 0 }}>3. Map <code>{report.display_name}</code> columns → {report.target_table}</h3>
      <p style={{ fontSize: 12, color: 'var(--text3)', margin: '0 0 10px', maxWidth: 720 }}>
        SAP-style: map once, then just upload data. This report is <MapBadge r={report} />.
        {report.source !== 'none' && ' You only need to re-map if your file uses different column headers than the built-in layout.'}
      </p>
      <div style={{ display: 'flex', gap: 8, marginBottom: 10, flexWrap: 'wrap', alignItems: 'center' }}>
        <input ref={fileRef} type="file" accept=".xlsx,.xls,.csv" style={{ display: 'none' }} onChange={e => { const f = e.target.files?.[0]; if (f) detect(f); e.target.value = '' }} />
        <button className="btn btn-secondary" style={{ fontSize: 13 }} disabled={busy} onClick={() => fileRef.current?.click()}>{busy ? '⏳ Reading…' : '📄 Upload a sample to auto-detect'}</button>
        <button className="btn btn-primary" style={{ fontSize: 13 }} onClick={save}>Save mapping</button>
        {report.source !== 'none' && <button className="btn btn-secondary" style={{ fontSize: 13 }} onClick={useDefault}>Use built-in default →</button>}
      </div>
      {detected.length > 0 && <p style={{ fontSize: 12, color: 'var(--text2)' }}>Sample spans <b>{detected.length}</b> month(s): {detected.join(', ')}.</p>}
      {fields.length > 0 && (
        <table style={{ width: '100%', borderCollapse: 'collapse', maxWidth: 720 }}>
          <thead><tr style={{ background: 'var(--surface2)' }}>{['Destination field', 'Type', 'Your column'].map(h => <th key={h} style={{ textAlign: 'left', padding: '6px 8px', fontSize: 11, color: 'var(--text2)' }}>{h}</th>)}</tr></thead>
          <tbody>
            {fields.map(f => (
              <tr key={f.col}>
                <td style={cell}><code>{f.col}</code>{(f as any).label && (f as any).label !== f.col ? <span style={{ color: 'var(--text3)', fontSize: 12 }}> · {(f as any).label}</span> : null}</td>
                <td style={{ ...cell, color: 'var(--text3)', fontSize: 12 }}>{f.type}</td>
                <td style={cell}>
                  <EntityPicker
                    options={(() => { const o = headers.map(h => ({ id: h, label: h })); const cur = src[f.col]; if (cur && !o.some(x => x.id === cur)) o.unshift({ id: cur, label: cur }); return o })()}
                    value={src[f.col] || null} allowCreate width="100%"
                    onChange={v => setSrc(p => ({ ...p, [f.col]: v || '' }))}
                    onCreate={v => setSrc(p => ({ ...p, [f.col]: v }))}
                    placeholder="(unmapped)" ariaLabel="Your column" />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {fields.length === 0 && <p style={{ fontSize: 13, color: 'var(--text3)' }}>Upload a sample to detect this report&apos;s columns.</p>}
    </div>
  )
}

// ── Step 4: upload data — HISTORICAL (multi-month) or APPEND (dedup-and-add) + optional date scope ─
function UploadStep({ carrierId, report, setMsg, onRemap }: { carrierId: string; report: ReportInfo; setMsg: (s: string) => void; onRemap: () => void }) {
  const [mode, setMode] = useState<'append' | 'historical'>('append')
  const [scope, setScope] = useState<'all' | 'day' | 'week' | 'custom'>('all')
  const [day, setDay] = useState('')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<any>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  // When this report last received data — from ANY path (this page, the portal pull, an email sweep).
  // /manual-upload/ingest traces under the report_key itself, so the key needs no mapping.
  const keys = useMemo(() => [report.report_key], [report.report_key])
  const { last, loaded, reload: reloadLast } = useLastUploads(keys)

  function scopeRange(): { date_from: string; date_to: string } {
    if (scope === 'day' && day) return { date_from: day, date_to: day }
    if (scope === 'week' && day) {
      const d = new Date(day + 'T00:00:00'); const dow = d.getDay(); const mon = new Date(d); mon.setDate(d.getDate() - ((dow + 6) % 7))
      const sun = new Date(mon); sun.setDate(mon.getDate() + 6)
      const iso = (x: Date) => x.toISOString().slice(0, 10)
      return { date_from: iso(mon), date_to: iso(sun) }
    }
    if (scope === 'custom') return { date_from: from, date_to: to }
    return { date_from: '', date_to: '' }
  }

  async function ingest(file: File) {
    setBusy(true); setResult(null)
    const { date_from, date_to } = scopeRange()
    const fd = new FormData()
    fd.append('report_key', report.report_key); fd.append('carrier_id', carrierId); fd.append('mode', mode)
    if (date_from) fd.append('date_from', date_from); if (date_to) fd.append('date_to', date_to)
    fd.append('file', file)
    try {
      const r: any = await apiUpload('/api/v1/commcalc/manual-upload/ingest', fd)
      setResult(r)
      setMsg(`✅ ${mode === 'historical' ? 'Loaded' : 'Appended'} ${r?.saved ?? 0} row(s)${r?.dupes_dropped ? `, ${r.dupes_dropped} duplicate(s) skipped` : ''}.`)
      reloadLast()
    } catch (e: any) { setMsg('❌ Upload failed: ' + (e?.message || e)) } finally { setBusy(false) }
  }

  return (
    <div>
      <h3 style={{ marginTop: 0 }}>4. Upload data — <code>{report.display_name}</code></h3>
      <div style={{ background: 'var(--surface2)', border: '1px solid var(--border)', borderRadius: 8, padding: '8px 12px', fontSize: 12, marginBottom: 12, maxWidth: 720 }}>
        💵 <b>Ingest only.</b> This loads the file into <code>{report.target_table}</code>. No commission or residual is recalculated — the loaded numbers are presented for review before any recalc.
        <LastUploadLine rec={last[report.report_key]} loaded={loaded} />
      </div>

      <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap', marginBottom: 12 }}>
        <div>
          <div style={{ fontSize: 11, color: 'var(--text2)', marginBottom: 4 }}>Mode</div>
          <div style={{ display: 'flex', gap: 6 }}>
            <button className="btn" style={{ fontSize: 13, background: mode === 'append' ? '#2563eb' : 'var(--surface)', color: mode === 'append' ? '#fff' : 'inherit', border: '1px solid var(--border)' }} onClick={() => setMode('append')}>Append (dedup &amp; add)</button>
            <button className="btn" style={{ fontSize: 13, background: mode === 'historical' ? '#2563eb' : 'var(--surface)', color: mode === 'historical' ? '#fff' : 'inherit', border: '1px solid var(--border)' }} onClick={() => setMode('historical')}>Historical (multi-month)</button>
          </div>
          <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 4, maxWidth: 340 }}>
            {mode === 'append'
              ? 'Adds only rows not already present (dedup). Re-uploading the same file adds nothing.'
              : 'One file spanning many months — rows split to their real month; each covered month’s manual rows are replaced.'}
          </div>
        </div>
        <div>
          <div style={{ fontSize: 11, color: 'var(--text2)', marginBottom: 4 }}>Date scope (optional)</div>
          <select style={sel} value={scope} onChange={e => setScope(e.target.value as any)}>
            <option value="all">All rows in the file</option>
            <option value="day">A single day</option>
            <option value="week">A week (Mon–Sun)</option>
            <option value="custom">Custom range</option>
          </select>
          {(scope === 'day' || scope === 'week') && <input type="date" style={{ ...sel, marginLeft: 6 }} value={day} onChange={e => setDay(e.target.value)} />}
          {scope === 'custom' && <span style={{ marginLeft: 6 }}><input type="date" style={sel} value={from} onChange={e => setFrom(e.target.value)} /> → <input type="date" style={sel} value={to} onChange={e => setTo(e.target.value)} /></span>}
        </div>
      </div>

      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <input ref={fileRef} type="file" accept=".xlsx,.xls,.csv" style={{ display: 'none' }} onChange={e => { const f = e.target.files?.[0]; if (f) ingest(f); e.target.value = '' }} />
        <button className="btn" style={{ fontSize: 13, background: '#16a34a', color: '#fff' }} disabled={busy} onClick={() => fileRef.current?.click()}>{busy ? '⏳ Uploading…' : '⬆️ Choose file & upload'}</button>
        <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={onRemap}>Re-map columns</button>
        <Link href="/commcalc/upload#trace" style={{ fontSize: 12 }}>🩺 Ingest health →</Link>
      </div>

      {result && (
        <div className="card" style={{ padding: 12, marginTop: 14, maxWidth: 720 }}>
          <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 6 }}>
            {result.mode === 'historical' ? 'Loaded' : 'Appended'} {result.saved} row(s) from {result.rows_in} in the file
          </div>
          <div style={{ fontSize: 12, color: 'var(--text2)', display: 'grid', gap: 3 }}>
            {result.dupes_dropped ? <div>· {result.dupes_dropped} duplicate row(s) skipped</div> : null}
            {(result.replaced_periods || []).length ? <div>· Replaced months: {result.replaced_periods.join(', ')}</div> : null}
            {result.date_span && result.date_span[0] ? <div>· Span: {result.date_span[0]} → {result.date_span[1]}</div> : null}
            {result.periods && Object.keys(result.periods).length ? <div>· Per month: {Object.entries(result.periods).map(([p, n]) => `${p}: ${n}`).join(' · ')}</div> : null}
            {result.linkage ? <div>· 🔗 {result.linkage.key}: {result.linkage.matched}/{result.linkage.distinct} order(s) linked ({result.linkage.unmatched} unlinked)</div> : null}
          </div>
          <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 8 }}>{result.money_note}</div>
        </div>
      )}
    </div>
  )
}
