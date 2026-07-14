'use client'
import { useState, useEffect, useCallback, useRef } from 'react'
import Link from 'next/link'
import { api, apiUpload, ORG_ID } from '@/lib/client'
import { readUploadOutcome, UploadGuardBanner, type UploadOutcome } from '../_lib/uploadGuard'
import EntityPicker from '@/components/EntityPicker'

// Implementation Wizard — onboard a new company's data end-to-end: map EVERY source report they
// upload (auto-detect columns from a sample) → see exactly which DESIRED OUTPUT reports (Commissions,
// Gross Profit / P&L, Total Comp, Pay Discrepancy) light up once the required inputs are mapped.

const REPORT_LABELS: Record<string, string> = {
  sales: 'Sales Transactions', payment_detail: 'Commission Payment Detail',
  mi_report: 'MI & ATU Subscriber Detail', comp_report: 'Comprehensive Compensation',
  carrier_commission: 'Carrier Commission Statement (Total/VidaPay, any carrier)',
}
const sel: React.CSSProperties = { padding: '6px 8px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const cell: React.CSSProperties = { padding: '6px 8px', borderTop: '1px solid var(--border)', fontSize: 13 }

export default function ImplementationWizard() {
  const [carriers, setCarriers] = useState<any[]>([])
  const [carrierId, setCarrierId] = useState('')
  const [readiness, setReadiness] = useState<any>(null)
  const [msg, setMsg] = useState('')

  useEffect(() => { api('/api/v1/commcalc/carriers').then((c: any) => setCarriers(c || [])).catch(() => {}) }, [])
  const loadReadiness = useCallback(() => {
    api(`/api/v1/commcalc/column-mapping/readiness?org_id=${ORG_ID}${carrierId ? `&carrier_id=${carrierId}` : ''}`)
      .then(setReadiness).catch(() => setReadiness(null))
  }, [carrierId])
  useEffect(() => { loadReadiness() }, [loadReadiness])

  const reports = readiness?.reports || {}
  const outputs = readiness?.outputs || {}
  const reportKeys = Object.keys(reports)
  const readyOut = Object.values(outputs).filter((o: any) => o.ready).length
  // Display name for a report key: the company's custom label (if set), else our default, else the key.
  const labelFor = (rk: string) => reports[rk]?.label || REPORT_LABELS[rk] || rk

  return (
    <div style={{ maxWidth: 900 }}>
      <div style={{ marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🧩 Implementation Wizard</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          Map every report this company uploads to our fields, and we&apos;ll produce the reports you want.
          Upload a sample of each file — we auto-detect the columns; you confirm and save.
        </p>
      </div>

      <div className="card" style={{ padding: 12, marginBottom: 14, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <b style={{ fontSize: 13 }}>Carrier mapping set:</b>
        <select style={sel} value={carrierId} onChange={e => setCarrierId(e.target.value)}>
          <option value="">Default (all carriers)</option>
          {carriers.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
        <span style={{ fontSize: 12, color: 'var(--text3)' }}>Pick a carrier to keep a separate column layout for it (e.g. Cricket vs Boost).</span>
        <span style={{ flex: 1 }} />
        <Link href="/commcalc/onboarding" style={{ fontSize: 13 }}>Full onboarding →</Link>
      </div>

      {/* desired outputs readiness */}
      <div className="card" style={{ padding: 16, marginBottom: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10, flexWrap: 'wrap' }}>
          <div style={{ fontWeight: 700, fontSize: 15 }}>📊 Reports you&apos;ll get</div>
          <span style={{ fontSize: 13, color: 'var(--text3)' }}>{readyOut}/{Object.keys(outputs).length} ready</span>
        </div>
        <div style={{ display: 'grid', gap: 8 }}>
          {Object.entries(outputs).map(([name, o]: any) => (
            <div key={name} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 10px', borderRadius: 8,
              background: o.ready ? '#f0fdf4' : 'var(--surface2)', border: `1px solid ${o.ready ? '#bbf7d0' : 'var(--border)'}` }}>
              <span style={{ fontWeight: 600, minWidth: 170 }}>{name}</span>
              {o.ready
                ? <span style={{ color: '#15803d', fontSize: 13, fontWeight: 600 }}>✅ Ready</span>
                : <span style={{ color: '#b45309', fontSize: 13 }}>Needs: {o.missing.map((m: string) => labelFor(m)).join(', ')}</span>}
              <span style={{ flex: 1 }} />
              <span style={{ fontSize: 11, color: 'var(--text3)' }}>from: {o.needs.map((m: string) => labelFor(m)).join(' + ')}</span>
            </div>
          ))}
        </div>
      </div>

      {/* source reports to map */}
      <div className="card" style={{ padding: 16 }}>
        <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 4 }}>🗂️ Map your source reports</div>
        <p style={{ fontSize: 12, color: 'var(--text3)', marginTop: 0 }}>Each report below feeds the outputs above. Expand one, upload a sample to auto-detect + Save the mappings, then set a period and Import the full file — the outputs above compute from it.</p>
        {reportKeys.length === 0 && <div style={{ color: 'var(--text3)', fontSize: 13 }}>Loading…</div>}
        {reportKeys.map(rk => (
          <ReportMapper key={rk} reportKey={rk} info={reports[rk]} carrierId={carrierId}
            onSaved={() => { loadReadiness(); setMsg('✅ Mappings saved.') }} setMsg={setMsg} />
        ))}
      </div>
      {msg && <div style={{ marginTop: 12, fontSize: 13 }}>{msg}</div>}
    </div>
  )
}

function ReportMapper({ reportKey, info, carrierId, onSaved, setMsg }:
  { reportKey: string; info: any; carrierId: string; onSaved: () => void; setMsg: (s: string) => void }) {
  const [open, setOpen] = useState(false)
  const [fields, setFields] = useState<any[]>([])
  const [src, setSrc] = useState<Record<string, string>>({})
  const [headers, setHeaders] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [editing, setEditing] = useState(false)
  const [nameVal, setNameVal] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)
  const importRef = useRef<HTMLInputElement>(null)
  const [importing, setImporting] = useState(false)
  const [period, setPeriod] = useState('')
  const [outcome, setOutcome] = useState<UploadOutcome | null>(null)
  const displayName = info?.label || REPORT_LABELS[reportKey] || reportKey

  // Rename / label this report. PATCH the existing definition (safe — touches only the label) or
  // create one if the company has none yet for this report, so onboarding can name each report.
  async function saveName() {
    const lbl = nameVal.trim()
    setBusy(true)
    try {
      if (info?.def_id) await api(`/api/v1/commcalc/report-definitions/${info.def_id}`, { method: 'PATCH', body: JSON.stringify({ label: lbl }) })
      else await api('/api/v1/commcalc/report-definitions', { method: 'POST', body: JSON.stringify({ report_key: reportKey, label: lbl }) })
      setEditing(false); setMsg(`✅ Report name saved: “${lbl || reportKey}”.`); onSaved()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) } finally { setBusy(false) }
  }

  const load = useCallback(() => {
    api(`/api/v1/commcalc/column-mapping/targets?report_key=${encodeURIComponent(reportKey)}`).then((t: any) => setFields(t?.fields || [])).catch(() => {})
    api(`/api/v1/commcalc/column-mapping?report_key=${encodeURIComponent(reportKey)}${carrierId ? `&carrier_id=${carrierId}` : ''}`).then((rules: any) => {
      const s: Record<string, string> = {}
      for (const r of rules || []) if (carrierId ? r.carrier_id === carrierId : !r.carrier_id) s[r.target_field] = r.source_header
      setSrc(s)
    }).catch(() => {})
  }, [reportKey, carrierId])
  useEffect(() => { if (open) load() }, [open, load])

  async function detect(file: File) {
    setBusy(true)
    const fd = new FormData(); fd.append('report_key', reportKey); if (carrierId) fd.append('carrier_id', carrierId); fd.append('file', file)
    try {
      const d: any = await apiUpload('/api/v1/commcalc/column-mapping/detect', fd)
      setHeaders(d?.headers || [])
      const s: Record<string, string> = {}; for (const sg of d?.suggestions || []) if (sg.suggested_source) s[sg.target_field] = sg.suggested_source
      setSrc(prev => ({ ...prev, ...s }))
      setMsg(`🔍 ${reportKey}: detected ${d?.headers?.length || 0} columns; matches pre-filled — review & Save.`)
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) } finally { setBusy(false) }
  }
  async function seed() {
    try { const d: any = await api(`/api/v1/commcalc/column-mapping/seed?report_key=${encodeURIComponent(reportKey)}${carrierId ? `&carrier_id=${carrierId}` : ''}`, { method: 'POST' }); setMsg(`✅ Seeded ${d?.seeded ?? 0} defaults.`); load(); onSaved() } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }
  async function saveAll() {
    setBusy(true); let n = 0
    for (const f of fields) {
      const sh = src[f.target_field]?.trim(); if (!sh) continue
      try { await api('/api/v1/commcalc/column-mapping', { method: 'POST', body: JSON.stringify({ report_key: reportKey, carrier_id: carrierId || undefined, target_field: f.target_field, source_header: sh, transform: f.transform }) }); n++ } catch { /* keep going */ }
    }
    setBusy(false); setMsg(`✅ ${reportKey}: saved ${n} mapping(s).`); onSaved()
  }
  // Load the FULL file into the system using the saved mappings (the step that was missing — the
  // wizard only ever sampled + saved rules, so mapped files never actually ingested). Feeds GP/comm.
  async function importFile(file: File) {
    if (!period.trim()) { setMsg('⚠️ Enter the period (e.g. “June 2026”) for this file before importing.'); return }
    setImporting(true); setOutcome(null)
    const fd = new FormData()
    fd.append('report_key', reportKey)
    if (carrierId) fd.append('carrier_id', carrierId)
    fd.append('period', period.trim())
    fd.append('file', file)
    try {
      const r: any = await apiUpload('/api/v1/commcalc/upload-mapped', fd)
      // The ingest guards (price-coverage refusal / row-count shrink) return HTTP-200; render them
      // honestly instead of a green "✅ 0 row(s)" that looks identical to a broken upload.
      const o = readUploadOutcome(r, 'row(s)')
      setOutcome(o.tone === 'ok' ? null : o)
      setMsg(o.tone === 'ok'
        ? `✅ ${reportKey}: imported ${o.saved} row(s) for ${period.trim()}. The reports above now compute from this data.`
        : `⚠️ ${reportKey}: ${o.text}`)
      onSaved()
    } catch (e: any) { setMsg('❌ Import failed: ' + (e?.message || e)) } finally { setImporting(false) }
  }

  const pct = info?.required ? Math.round(100 * info.required_mapped / info.required) : 0
  return (
    <div style={{ borderTop: '1px solid var(--border)', padding: '10px 0' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer', flexWrap: 'wrap' }} onClick={() => setOpen(o => !o)}>
        <span style={{ width: 16, color: 'var(--text3)' }}>{open ? '▾' : '▸'}</span>
        {editing ? (
          <span style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }} onClick={e => e.stopPropagation()}>
            <input autoFocus style={{ ...sel, width: 220 }} value={nameVal} placeholder={REPORT_LABELS[reportKey] || reportKey}
              onChange={e => setNameVal(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') saveName(); if (e.key === 'Escape') setEditing(false) }} />
            <button className="btn btn-primary" style={{ fontSize: 12 }} disabled={busy} onClick={saveName}>Save</button>
            <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={() => setEditing(false)}>Cancel</button>
          </span>
        ) : (
          <span style={{ display: 'inline-flex', gap: 6, alignItems: 'center', minWidth: 200 }}>
            <b>{displayName}</b>
            <button title="Rename / label this report" onClick={e => { e.stopPropagation(); setNameVal(info?.label || ''); setEditing(true) }}
              style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: 12, color: 'var(--text3)', padding: 0 }}>✏️</button>
            <span style={{ fontSize: 11, color: 'var(--text3)' }}>({reportKey})</span>
          </span>
        )}
        {info?.ready
          ? <span style={{ fontSize: 12, color: '#15803d', background: '#f0fdf4', padding: '2px 8px', borderRadius: 10 }}>✅ ready</span>
          : <span style={{ fontSize: 12, color: '#b45309', background: '#fffbeb', padding: '2px 8px', borderRadius: 10 }}>{info?.required_mapped || 0}/{info?.required || 0} required mapped</span>}
        <div style={{ flex: 1, maxWidth: 160, height: 6, background: 'var(--border)', borderRadius: 4, overflow: 'hidden' }}>
          <div style={{ width: `${pct}%`, height: '100%', background: info?.ready ? '#22c55e' : '#f59e0b' }} />
        </div>
      </div>
      {open && (
        <div style={{ marginTop: 10, paddingLeft: 26 }}>
          <div style={{ display: 'flex', gap: 8, marginBottom: 8, flexWrap: 'wrap' }}>
            <button className="btn btn-secondary" style={{ fontSize: 13 }} disabled={busy} onClick={seed}>Seed default layout</button>
            <input ref={fileRef} type="file" accept=".xlsx,.xls,.csv" style={{ display: 'none' }} onChange={e => { const f = e.target.files?.[0]; if (f) detect(f); e.target.value = '' }} />
            <button className="btn btn-secondary" style={{ fontSize: 13 }} disabled={busy} onClick={() => fileRef.current?.click()}>📄 Upload sample to auto-detect</button>
            <button className="btn btn-primary" style={{ fontSize: 13 }} disabled={busy} onClick={saveAll}>Save mappings</button>
            <span style={{ width: 1, alignSelf: 'stretch', background: 'var(--border)', margin: '0 2px' }} />
            <input style={{ ...sel, width: 130 }} placeholder="Period e.g. June 2026" value={period} onChange={e => setPeriod(e.target.value)} />
            <input ref={importRef} type="file" accept=".xlsx,.xls,.csv" style={{ display: 'none' }} onChange={e => { const f = e.target.files?.[0]; if (f) importFile(f); e.target.value = '' }} />
            <button className="btn" style={{ fontSize: 13, background: '#16a34a', color: '#fff' }} disabled={importing || busy}
              onClick={() => importRef.current?.click()}
              title="Load the FULL file into the system using the saved mappings — the reports above then compute from it">
              {importing ? '⏳ Importing…' : '⬆️ Import file → load data'}
            </button>
          </div>
          <p style={{ fontSize: 11, color: 'var(--text3)', margin: '0 0 8px' }}>
            Two steps: (1) upload a sample → confirm the columns → <b>Save mappings</b>; then (2) set the period and
            <b> Import file → load data</b> to load the full file. {info?.ready ? '' : 'Map the required (*) fields first.'}
          </p>
          <UploadGuardBanner outcome={outcome} style={{ maxWidth: 620 }} />
          {fields.length === 0
            ? <p style={{ fontSize: 13, color: 'var(--text3)' }}>No default field registry for this report — map it on the full Column Mapping page.</p>
            : <table style={{ width: '100%', borderCollapse: 'collapse', maxWidth: 620 }}>
                <thead><tr style={{ background: 'var(--surface2)' }}>{['Our field', 'Your column'].map(h => <th key={h} style={{ textAlign: 'left', padding: '6px 8px', fontSize: 11, color: 'var(--text2)' }}>{h}</th>)}</tr></thead>
                <tbody>
                  {fields.map(f => (
                    <tr key={f.target_field}>
                      <td style={cell}>{f.label}{f.required && <span style={{ color: '#b42318' }}> *</span>}</td>
                      {/* RULE THREE §3b: pick the detected column; allowCreate keeps manual entry pre-detect. */}
                      <td style={cell}>
                        <EntityPicker
                          options={(() => { const o = headers.map(h => ({ id: h, label: h })); const cur = src[f.target_field]; if (cur && !o.some(x => x.id === cur)) o.unshift({ id: cur, label: cur }); return o })()}
                          value={src[f.target_field] || null} allowCreate width="100%"
                          onChange={v => setSrc(p => ({ ...p, [f.target_field]: v || '' }))}
                          onCreate={v => setSrc(p => ({ ...p, [f.target_field]: v }))}
                          placeholder="(unmapped)" ariaLabel="Your column" />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>}
        </div>
      )}
    </div>
  )
}
