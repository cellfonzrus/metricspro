'use client'
import { useState, useEffect, useCallback, useRef } from 'react'
import Link from 'next/link'
import { api } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'

// Onboarding wizard (A3): one guided flow to bring a NEW carrier/company online end-to-end —
// company → carrier → connector + reports → upload a sample + map its columns → map comp categories
// → match stores → activate. Orchestrates the pieces that already live on separate pages (companies,
// carriers, connectors, column-mapping, carrier-mapping, store-match) and carries context between them.

const sel: React.CSSProperties = { padding: '6px 8px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const cell: React.CSSProperties = { padding: '6px 8px', borderTop: '1px solid var(--border)', fontSize: 13 }
const STEPS = ['Company', 'Carrier', 'Connector & reports', 'Upload & columns', 'Categories', 'Stores', 'Activate']
const SWEEP_KINDS = ['manual', 'epay', 'vip', 'dlar', 'b2b', 'google_closing']

export default function OnboardingWizard() {
  const { period } = usePeriod()
  const [step, setStep] = useState(0)
  const [msg, setMsg] = useState('')

  // selections carried across steps
  const [companies, setCompanies] = useState<any[]>([])
  const [companyId, setCompanyId] = useState('')
  const [carriers, setCarriers] = useState<any[]>([])
  const [carrierId, setCarrierId] = useState('')
  const [connectors, setConnectors] = useState<any[]>([])
  const [connectorId, setConnectorId] = useState('')
  const [report, setReport] = useState<any>(null)   // {report_key, target_table, id, label}

  // add-forms
  const [coAdd, setCoAdd] = useState({ name: '', legal_name: '', ein: '' })
  const [carAdd, setCarAdd] = useState({ name: '', code: '' })
  const [connAdd, setConnAdd] = useState({ vendor_name: '', label: '', sweep_kind: 'manual', portal_url: '' })
  const [repAdd, setRepAdd] = useState({ report_key: '', label: '', target_table: '', source_name: '', upload_endpoint: '', source_url: '', period_mode: 'data' })

  const loadCompanies = useCallback(() => api('/api/v1/account/companies').then((d: any) => setCompanies(d?.companies || d || [])).catch(() => {}), [])
  const loadCarriers = useCallback(() => api('/api/v1/commcalc/carriers').then((c: any) => setCarriers(c || [])).catch(() => {}), [])
  const loadConnectors = useCallback(() => api('/api/v1/commcalc/connectors').then((c: any) => setConnectors(c || [])).catch(() => {}), [])
  useEffect(() => { loadCompanies(); loadCarriers(); loadConnectors() }, [loadCompanies, loadCarriers, loadConnectors])

  const connector = connectors.find(c => c.id === connectorId)

  // ── step actions ────────────────────────────────────────────────────────────────────────────
  async function addCompany() {
    if (!coAdd.name.trim()) { setMsg('Company name required.'); return }
    try { const r: any = await api('/api/v1/account/companies', { method: 'POST', body: JSON.stringify(coAdd) }); setCoAdd({ name: '', legal_name: '', ein: '' }); await loadCompanies(); if (r?.id) setCompanyId(r.id); setMsg('✅ Company added.') } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }
  async function addCarrier() {
    if (!carAdd.name.trim()) { setMsg('Carrier name required.'); return }
    try { const r: any = await api('/api/v1/commcalc/carriers', { method: 'POST', body: JSON.stringify(carAdd) }); setCarAdd({ name: '', code: '' }); await loadCarriers(); if (r?.id) setCarrierId(r.id); setMsg('✅ Carrier added.') } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }
  async function addConnector() {
    if (!connAdd.vendor_name.trim()) { setMsg('Vendor name required.'); return }
    try { const r: any = await api('/api/v1/commcalc/connectors', { method: 'POST', body: JSON.stringify({ ...connAdd, carrier_id: carrierId || undefined }) }); setConnAdd({ vendor_name: '', label: '', sweep_kind: 'manual', portal_url: '' }); await loadConnectors(); if (r?.id) setConnectorId(r.id); setMsg('✅ Connector added.') } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }
  async function addReport() {
    if (!connectorId) { setMsg('Pick or add a connector first.'); return }
    if (!repAdd.report_key.trim()) { setMsg('Report key required.'); return }
    try {
      const r: any = await api('/api/v1/commcalc/report-definitions', { method: 'POST', body: JSON.stringify({ ...repAdd, connector_id: connectorId }) })
      setRepAdd({ report_key: '', label: '', target_table: '', source_name: '', upload_endpoint: '', source_url: '', period_mode: 'data' })
      await loadConnectors(); if (r?.report_key || r?.id) setReport(r); setMsg('✅ Report added.')
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }
  async function activate() {
    try {
      if (connectorId) await api(`/api/v1/commcalc/connectors/${connectorId}`, { method: 'PATCH', body: JSON.stringify({ enabled: true }) })
      if (report?.id) await api(`/api/v1/commcalc/report-definitions/${report.id}`, { method: 'PATCH', body: JSON.stringify({ auto: true }) })
      setMsg('✅ Activated. The connector is enabled' + (report?.id ? ' and the report is set to auto-sweep.' : '.'))
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }

  const canNext = [!!companyId, !!carrierId, !!connectorId, true, true, true, true][step]

  return (
    <div>
      <div style={{ marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🚀 Carrier / Company Onboarding</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          Bring a new carrier or company online end-to-end — no SQL, no code. Each step reuses the same tools you&apos;d use individually, in order.
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
        {/* STEP 1 — Company */}
        {step === 0 && (
          <div>
            <h3 style={{ marginTop: 0 }}>1. Choose or add a company</h3>
            <div style={{ display: 'grid', gap: 6, maxWidth: 520 }}>
              {companies.map(c => (
                <label key={c.id} style={{ display: 'flex', gap: 8, alignItems: 'center', padding: 6, borderRadius: 6, background: companyId === c.id ? 'var(--surface2)' : 'transparent' }}>
                  <input type="radio" name="co" checked={companyId === c.id} onChange={() => setCompanyId(c.id)} />
                  <b>{c.name}</b> <span style={{ color: 'var(--text3)', fontSize: 12 }}>{c.legal_name || ''} {c.ein ? `· EIN ${c.ein}` : ''}</span>
                </label>
              ))}
            </div>
            <div style={{ marginTop: 12, display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
              <input style={{ ...sel, width: 180 }} placeholder="New company name" value={coAdd.name} onChange={e => setCoAdd({ ...coAdd, name: e.target.value })} />
              <input style={{ ...sel, width: 160 }} placeholder="Legal name (opt)" value={coAdd.legal_name} onChange={e => setCoAdd({ ...coAdd, legal_name: e.target.value })} />
              <input style={{ ...sel, width: 110 }} placeholder="EIN (opt)" value={coAdd.ein} onChange={e => setCoAdd({ ...coAdd, ein: e.target.value })} />
              <button className="btn btn-primary" style={{ fontSize: 13 }} onClick={addCompany}>+ Add</button>
            </div>
          </div>
        )}

        {/* STEP 2 — Carrier */}
        {step === 1 && (
          <div>
            <h3 style={{ marginTop: 0 }}>2. Choose or add a carrier</h3>
            <div style={{ display: 'grid', gap: 6, maxWidth: 520 }}>
              {carriers.map(c => (
                <label key={c.id} style={{ display: 'flex', gap: 8, alignItems: 'center', padding: 6, borderRadius: 6, background: carrierId === c.id ? 'var(--surface2)' : 'transparent' }}>
                  <input type="radio" name="car" checked={carrierId === c.id} onChange={() => setCarrierId(c.id)} />
                  <b>{c.name}</b> {c.is_default && <span style={{ fontSize: 11, color: 'var(--text3)' }}>(default)</span>} {c.code && <span style={{ fontSize: 11, color: 'var(--text3)' }}>· {c.code}</span>}
                </label>
              ))}
            </div>
            <div style={{ marginTop: 12, display: 'flex', gap: 6, alignItems: 'center' }}>
              <input style={{ ...sel, width: 180 }} placeholder="New carrier, e.g. Cricket" value={carAdd.name} onChange={e => setCarAdd({ ...carAdd, name: e.target.value })} />
              <input style={{ ...sel, width: 110 }} placeholder="code (opt)" value={carAdd.code} onChange={e => setCarAdd({ ...carAdd, code: e.target.value })} />
              <button className="btn btn-primary" style={{ fontSize: 13 }} onClick={addCarrier}>+ Add</button>
            </div>
          </div>
        )}

        {/* STEP 3 — Connector & reports */}
        {step === 2 && (
          <div>
            <h3 style={{ marginTop: 0 }}>3. Connect the data source &amp; its reports</h3>
            <div style={{ display: 'grid', gap: 6, maxWidth: 640 }}>
              {connectors.map(c => (
                <label key={c.id} style={{ display: 'flex', gap: 8, alignItems: 'center', padding: 6, borderRadius: 6, background: connectorId === c.id ? 'var(--surface2)' : 'transparent' }}>
                  <input type="radio" name="conn" checked={connectorId === c.id} onChange={() => { setConnectorId(c.id); setReport(null) }} />
                  <b>{c.label || c.vendor_name}</b> <span style={{ fontSize: 11, color: 'var(--text3)' }}>· {c.sweep_kind} · {(c.reports || []).length} report(s) {c.enabled ? '· enabled' : ''}</span>
                </label>
              ))}
            </div>
            <div style={{ marginTop: 10, display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
              <input style={{ ...sel, width: 150 }} placeholder="New vendor name" value={connAdd.vendor_name} onChange={e => setConnAdd({ ...connAdd, vendor_name: e.target.value })} />
              <input style={{ ...sel, width: 140 }} placeholder="label (opt)" value={connAdd.label} onChange={e => setConnAdd({ ...connAdd, label: e.target.value })} />
              <select style={sel} value={connAdd.sweep_kind} onChange={e => setConnAdd({ ...connAdd, sweep_kind: e.target.value })}>{SWEEP_KINDS.map(k => <option key={k} value={k}>{k}</option>)}</select>
              <input style={{ ...sel, width: 170 }} placeholder="portal url (opt)" value={connAdd.portal_url} onChange={e => setConnAdd({ ...connAdd, portal_url: e.target.value })} />
              <button className="btn btn-primary" style={{ fontSize: 13 }} onClick={addConnector}>+ Add connector</button>
            </div>

            {connector && (
              <div style={{ marginTop: 16, borderTop: '1px solid var(--border)', paddingTop: 12 }}>
                <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 6 }}>Reports for {connector.label || connector.vendor_name}</div>
                <div style={{ display: 'grid', gap: 4, maxWidth: 640 }}>
                  {(connector.reports || []).map((r: any) => (
                    <label key={r.id} style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 13 }}>
                      <input type="radio" name="rep" checked={report?.id === r.id} onChange={() => setReport(r)} />
                      <b>{r.report_key}</b> <span style={{ color: 'var(--text3)', fontSize: 12 }}>{r.label || ''} {r.target_table ? `→ ${r.target_table}` : ''}</span>
                    </label>
                  ))}
                  {(connector.reports || []).length === 0 && <span style={{ fontSize: 12, color: 'var(--text3)' }}>No reports yet — add one below.</span>}
                </div>
                <div style={{ marginTop: 10, display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
                  <input style={{ ...sel, width: 130 }} placeholder="report_key*" value={repAdd.report_key} onChange={e => setRepAdd({ ...repAdd, report_key: e.target.value })} />
                  <input style={{ ...sel, width: 140 }} placeholder="label" value={repAdd.label} onChange={e => setRepAdd({ ...repAdd, label: e.target.value })} />
                  <input style={{ ...sel, width: 150 }} placeholder="target_table" value={repAdd.target_table} onChange={e => setRepAdd({ ...repAdd, target_table: e.target.value })} />
                  <input style={{ ...sel, width: 150 }} placeholder="source report name" value={repAdd.source_name} onChange={e => setRepAdd({ ...repAdd, source_name: e.target.value })} />
                  <button className="btn btn-primary" style={{ fontSize: 13 }} onClick={addReport}>+ Add report</button>
                </div>
              </div>
            )}
          </div>
        )}

        {/* STEP 4 — Upload & columns */}
        {step === 3 && (
          <ColumnsStep reportKey={report?.report_key || ''} targetTable={report?.target_table || ''} carrierId={carrierId} setMsg={setMsg} />
        )}

        {/* STEP 5 — Categories */}
        {step === 4 && (
          <CategoriesStep carrierId={carrierId} period={period} setMsg={setMsg} />
        )}

        {/* STEP 6 — Stores */}
        {step === 5 && <StoresStep />}

        {/* STEP 7 — Activate */}
        {step === 6 && (
          <div>
            <h3 style={{ marginTop: 0 }}>7. Activate</h3>
            <ul style={{ fontSize: 14, lineHeight: 1.8 }}>
              <li>Company: <b>{companies.find(c => c.id === companyId)?.name || '—'}</b></li>
              <li>Carrier: <b>{carriers.find(c => c.id === carrierId)?.name || '—'}</b></li>
              <li>Connector: <b>{connector?.label || connector?.vendor_name || '—'}</b></li>
              <li>Report: <b>{report?.report_key || '—'}</b>{report?.target_table ? ` → ${report.target_table}` : ''}</li>
            </ul>
            <button className="btn btn-primary" onClick={activate}>✅ Enable connector{report?.id ? ' + auto-sweep this report' : ''}</button>
            <div style={{ marginTop: 10, fontSize: 13 }}><Link href="/commcalc/connectors">Manage in Connectors →</Link></div>
          </div>
        )}
      </div>

      {/* nav */}
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginTop: 14 }}>
        <button className="btn btn-secondary" disabled={step === 0} onClick={() => setStep(s => Math.max(0, s - 1))}>← Back</button>
        <button className="btn btn-primary" disabled={step === STEPS.length - 1 || !canNext} onClick={() => setStep(s => Math.min(STEPS.length - 1, s + 1))}>Next →</button>
        {!canNext && <span style={{ fontSize: 12, color: 'var(--text3)' }}>Select an option to continue.</span>}
        {msg && <span style={{ fontSize: 13, marginLeft: 'auto' }}>{msg}</span>}
      </div>
    </div>
  )
}

// ── Step 4: compact column mapper (reuses the A2 endpoints) ─────────────────────────────────────
function ColumnsStep({ reportKey, targetTable, carrierId, setMsg }: { reportKey: string; targetTable: string; carrierId: string; setMsg: (s: string) => void }) {
  const [fields, setFields] = useState<any[]>([])
  const [src, setSrc] = useState<Record<string, string>>({})
  const [headers, setHeaders] = useState<string[]>([])
  const fileRef = useRef<HTMLInputElement>(null)

  const load = useCallback(() => {
    if (!reportKey) return
    api(`/api/v1/commcalc/column-mapping/targets?report_key=${encodeURIComponent(reportKey)}`).then((t: any) => setFields(t?.fields || [])).catch(() => {})
    api(`/api/v1/commcalc/column-mapping?report_key=${encodeURIComponent(reportKey)}${carrierId ? `&carrier_id=${carrierId}` : ''}`).then((rules: any) => {
      const s: Record<string, string> = {}; for (const r of rules || []) if (carrierId ? r.carrier_id === carrierId : !r.carrier_id) s[r.target_field] = r.source_header
      setSrc(prev => ({ ...s, ...prev }))
    }).catch(() => {})
  }, [reportKey, carrierId])
  useEffect(() => { load() }, [load])

  async function seed() { try { const d: any = await api(`/api/v1/commcalc/column-mapping/seed?report_key=${encodeURIComponent(reportKey)}${carrierId ? `&carrier_id=${carrierId}` : ''}`, { method: 'POST' }); setMsg(`✅ Seeded ${d?.seeded ?? 0} defaults.`); load() } catch (e: any) { setMsg('❌ ' + (e?.message || e)) } }
  async function detect(file: File) {
    const fd = new FormData(); fd.append('report_key', reportKey); if (carrierId) fd.append('carrier_id', carrierId); fd.append('file', file)
    try { const d: any = await api('/api/v1/commcalc/column-mapping/detect', { method: 'POST', body: fd }); setHeaders(d?.headers || []); const s: Record<string, string> = {}; for (const sg of d?.suggestions || []) if (sg.suggested_source) s[sg.target_field] = sg.suggested_source; setSrc(prev => ({ ...prev, ...s })); setMsg(`🔍 Detected ${d?.headers?.length || 0} columns; matches pre-filled.`) } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }
  async function saveAll() {
    let n = 0
    for (const f of fields) { const sh = src[f.target_field]?.trim(); if (!sh) continue; try { await api('/api/v1/commcalc/column-mapping', { method: 'POST', body: JSON.stringify({ report_key: reportKey, carrier_id: carrierId || undefined, target_field: f.target_field, source_header: sh, transform: f.transform }) }); n++ } catch { /* keep going */ } }
    setMsg(`✅ Saved ${n} mapping(s).`)
  }

  if (!reportKey) return <div><h3 style={{ marginTop: 0 }}>4. Upload &amp; map columns</h3><p style={{ color: 'var(--text3)' }}>Pick a report in step 3 first.</p></div>
  return (
    <div>
      <h3 style={{ marginTop: 0 }}>4. Upload a sample &amp; map its columns → <code>{reportKey}</code>{targetTable ? ` (${targetTable})` : ''}</h3>
      <div style={{ display: 'flex', gap: 8, marginBottom: 10, flexWrap: 'wrap' }}>
        <button className="btn btn-secondary" style={{ fontSize: 13 }} onClick={seed}>Seed default layout</button>
        <input ref={fileRef} type="file" accept=".xlsx,.xls" style={{ display: 'none' }} onChange={e => { const f = e.target.files?.[0]; if (f) detect(f); e.target.value = '' }} />
        <button className="btn btn-secondary" style={{ fontSize: 13 }} onClick={() => fileRef.current?.click()}>📄 Upload sample to auto-detect</button>
        <button className="btn btn-primary" style={{ fontSize: 13 }} onClick={saveAll}>Save mappings</button>
        <Link href="/commcalc/column-mapping" style={{ fontSize: 13, alignSelf: 'center' }}>Fine-tune →</Link>
      </div>
      {fields.length === 0 && <p style={{ fontSize: 13, color: 'var(--text3)' }}>This is a new report key (no default field registry). Map its columns on the full Column Mapping page.</p>}
      {fields.length > 0 && (
        <table style={{ width: '100%', borderCollapse: 'collapse', maxWidth: 640 }}>
          <thead><tr style={{ background: 'var(--surface2)' }}>{['Our field', 'Source column'].map(h => <th key={h} style={{ textAlign: 'left', padding: '6px 8px', fontSize: 11, color: 'var(--text2)' }}>{h}</th>)}</tr></thead>
          <tbody>
            {fields.map(f => (
              <tr key={f.target_field}>
                <td style={cell}>{f.label}{f.required && <span style={{ color: '#b42318' }}> *</span>}</td>
                <td style={cell}><input list="ob-headers" style={{ ...sel, width: '100%' }} placeholder="(unmapped)" value={src[f.target_field] || ''} onChange={e => setSrc(p => ({ ...p, [f.target_field]: e.target.value }))} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {headers.length > 0 && <datalist id="ob-headers">{headers.map(h => <option key={h} value={h} />)}</datalist>}
    </div>
  )
}

// ── Step 5: category mapping pointer ────────────────────────────────────────────────────────────
function CategoriesStep({ carrierId, period, setMsg }: { carrierId: string; period: string; setMsg: (s: string) => void }) {
  const [unmapped, setUnmapped] = useState<any[]>([])
  useEffect(() => {
    if (!period) return
    api(`/api/v1/commcalc/carrier-category-map/unmapped?period=${encodeURIComponent(period)}${carrierId ? `&carrier_id=${carrierId}` : ''}`).then((d: any) => setUnmapped(d?.unmapped || [])).catch(() => {})
  }, [carrierId, period])
  return (
    <div>
      <h3 style={{ marginTop: 0 }}>5. Map comp categories → components</h3>
      <p style={{ fontSize: 13, color: 'var(--text2)' }}>
        Once this carrier&apos;s comp data is loaded, map each raw compensation category to RESIDUAL / COMMISSION / SPIFF / REIMBURSEMENT.
      </p>
      {unmapped.length > 0
        ? <p style={{ fontSize: 13 }}>⚠️ <b>{unmapped.length}</b> categories need mapping for {period}.</p>
        : <p style={{ fontSize: 13, color: 'var(--text3)' }}>No unmapped categories detected for {period} (or comp not loaded yet).</p>}
      <Link href="/commcalc/carrier-mapping" className="btn btn-primary" style={{ fontSize: 13, textDecoration: 'none' }}>Open Carrier Mapping →</Link>
    </div>
  )
}

// ── Step 6: store matching pointer ──────────────────────────────────────────────────────────────
function StoresStep() {
  const [n, setN] = useState<number | null>(null)
  useEffect(() => { api('/api/v1/commcalc/store-unmatched').then((d: any) => setN((d?.unmatched || d || []).length ?? 0)).catch(() => setN(null)) }, [])
  return (
    <div>
      <h3 style={{ marginTop: 0 }}>6. Match stores</h3>
      <p style={{ fontSize: 13, color: 'var(--text2)' }}>Map the raw store names in this carrier&apos;s feeds to your canonical stores so P&amp;L and reports attribute correctly.</p>
      {n !== null && <p style={{ fontSize: 13 }}>{n > 0 ? <>⚠️ <b>{n}</b> store names are currently unmatched.</> : '✅ No unmatched stores.'}</p>}
      <Link href="/commcalc/store-match" className="btn btn-primary" style={{ fontSize: 13, textDecoration: 'none' }}>Open Store Matching →</Link>
    </div>
  )
}
